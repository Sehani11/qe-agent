"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { SSELogEvent, SSECompleteEvent } from "../types/session";
import type { VerificationVerdict } from "../types/verification";

interface UseSSEStreamOptions {
    onStatusChange?: (isStreaming: boolean) => void;
    onComplete?: (sessionId: string, acceptanceCriteria?: string) => void;
    onError?: (error: string) => void;
    onLog?: (log: SSELogEvent) => void;
    /** Called for each "verdict" SSE event from the verification endpoint. */
    onVerdict?: (verdict: VerificationVerdict) => void;
    /** Called when a verification "complete" event arrives (has total/passed/failed). */
    onVerificationComplete?: (total: number, passed: number, failed: number) => void;
}

export function useSSEStream(options?: UseSSEStreamOptions) {
    const [logs, setLogs] = useState<SSELogEvent[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const abortControllerRef = useRef<AbortController | null>(null);
    const isStreamingRef = useRef(false);
    // Keep a ref to the latest options so connect() doesn't need them as a dependency.
    // This prevents connect() from being re-created on every render when callers
    // pass an inline options object.
    const optionsRef = useRef(options);
    useEffect(() => {
        optionsRef.current = options;
    });

    // Sync ref with state for handlers
    useEffect(() => {
        isStreamingRef.current = isStreaming;
        optionsRef.current?.onStatusChange?.(isStreaming);
    }, [isStreaming]);

    const connect = useCallback(async (
        url: string,
        method: "GET" | "POST" = "GET",
        payload?: Record<string, unknown>,
        headers: Record<string, string> = {}
    ) => {
        if (isStreamingRef.current) return;

        // Clear previous state and setup tracking
        setError(null);
        setLogs([]);
        setIsStreaming(true);

        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        abortControllerRef.current = new AbortController();

        try {
            const fetchOptions: RequestInit = {
                method,
                headers: {
                    "Accept": "text/event-stream",
                    ...headers,
                },
                signal: abortControllerRef.current.signal,
            };

            if (method === "POST" && payload) {
                fetchOptions.headers = {
                    ...fetchOptions.headers,
                    "Content-Type": "application/json",
                };
                fetchOptions.body = JSON.stringify(payload);
            }

            const response = await fetch(url, fetchOptions);

            if (!response.ok || !response.body) {
                throw new Error(`Connection failed with status: ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");

            // SSE chunk accumulation buffer
            let buffer = "";

            while (true) {
                const { value, done } = await reader.read();

                if (done) {
                    setIsStreaming(false);
                    break;
                }

                const chunk = decoder.decode(value, { stream: true });
                buffer += chunk;

                const lines = buffer.split("\n\n");
                // Keep the last partial chunk in the buffer if it didn't end with \n\n
                buffer = lines.pop() ?? "";

                for (const line of lines) {
                    if (line.startsWith("data: ")) {
                        try {
                            const dataStr = line.substring(6);
                            if (!dataStr.trim()) continue;

                            const json = JSON.parse(dataStr) as { type: string; [key: string]: unknown };

                            if (json.type === "log") {
                                const logEvent = json as unknown as SSELogEvent;
                                setLogs((prev) => [...prev, logEvent]);
                                optionsRef.current?.onLog?.(logEvent);
                            } else if (json.type === "complete") {
                                setIsStreaming(false);
                                // Distinguish pipeline complete (has session_id) from verification complete (has total)
                                if ("session_id" in json) {
                                    const completeEvent = json as unknown as SSECompleteEvent;
                                    optionsRef.current?.onComplete?.(completeEvent.session_id, completeEvent.acceptance_criteria);
                                } else if ("total" in json) {
                                    optionsRef.current?.onVerificationComplete?.(
                                        json.total as number,
                                        json.passed as number,
                                        json.failed as number,
                                    );
                                }
                            } else if (json.type === "error") {
                                setIsStreaming(false);
                                setError(json.message as string);
                                optionsRef.current?.onError?.(json.message as string);
                            } else if (json.type === "verdict") {
                                optionsRef.current?.onVerdict?.(json as unknown as VerificationVerdict);
                            }
                        } catch (err) {
                            console.error("SSE Parse Exception:", err);
                        }
                    }
                }
            }
        } catch (err: unknown) {
            if (err instanceof Error && err.name === "AbortError") {
                console.log("Stream aborted manually.");
                return;
            }
            const errorMessage = err instanceof Error ? err.message : "Stream connection failed.";
            setError(errorMessage);
            setIsStreaming(false);
            optionsRef.current?.onError?.(errorMessage);
        }
    }, []);

    const disconnect = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
        }
        setIsStreaming(false);
    }, []);

    useEffect(() => {
        return () => {
            disconnect();
        };
    }, [disconnect]);

    return { connect, disconnect, logs, isStreaming, error };
}
