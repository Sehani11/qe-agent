"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { supabase } from "@/lib/supabase/client";
import type { ChatMessage } from "@/lib/types/chat";
import { useModelSelection } from "@/providers/ModelProvider";
import { useSSEStream } from "./useSSEStream";

/** Fetch a session's chat history from GET /api/v1/chat/{id}/messages (Story 5.2). */
export function useChatHistory(sessionId: string | null) {
    return useQuery({
        queryKey: ["chat", sessionId],
        queryFn: async () => {
            const { data } = await apiClient.get<ChatMessage[]>(
                `/chat/${sessionId}/messages`
            );
            return data;
        },
        enabled: !!sessionId,
        retry: false,
    });
}

/**
 * Orchestrates the RAG chat for a session: seeds from history, sends questions
 * over SSE (POST /api/v1/chat/message), and accumulates streamed tokens into a
 * single assistant bubble.
 */
export function useChat(sessionId: string | null) {
    const { data: history, isLoading: isHistoryLoading } = useChatHistory(sessionId);
    const selection = useModelSelection();
    // Messages sent during this mount. Rendered after the fetched history, so we
    // never copy history into state (avoids set-state-in-effect and revisit dupes).
    // The panel is remounted per session (keyed route), so this resets on switch.
    const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
    const [error, setError] = useState<string | null>(null);

    const messages = useMemo(
        () => [...(history ?? []), ...localMessages],
        [history, localMessages]
    );

    const { connect, isStreaming } = useSSEStream({
        onToken: useCallback((content: string) => {
            setLocalMessages((prev) => {
                if (prev.length === 0) return prev;
                const last = prev[prev.length - 1];
                if (last.role !== "assistant") return prev;
                const updated = { ...last, content: last.content + content };
                return [...prev.slice(0, -1), updated];
            });
        }, []),
        onError: useCallback((message: string) => {
            setError(message);
            // Drop the empty assistant placeholder so no blank bubble lingers.
            setLocalMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === "assistant" && last.content === "") {
                    return prev.slice(0, -1);
                }
                return prev;
            });
        }, []),
    });

    const sendMessage = useCallback(
        async (question: string) => {
            const q = question.trim();
            if (!sessionId || !q || isStreaming) return;

            setError(null);
            const now = new Date().toISOString();
            const userMsg: ChatMessage = {
                id: crypto.randomUUID(),
                session_id: sessionId,
                role: "user",
                content: q,
                created_at: now,
            };
            const assistantMsg: ChatMessage = {
                id: crypto.randomUUID(),
                session_id: sessionId,
                role: "assistant",
                content: "",
                created_at: now,
            };
            setLocalMessages((prev) => [...prev, userMsg, assistantMsg]);

            const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
            const {
                data: { session },
            } = await supabase.auth.getSession();
            const authHeaders: Record<string, string> = session?.access_token
                ? { Authorization: `Bearer ${session.access_token}` }
                : {};

            connect(
                `${apiBaseUrl}/api/v1/chat/message`,
                "POST",
                { session_id: sessionId, question: q, ...selection },
                authHeaders
            );
        },
        [sessionId, isStreaming, connect, selection]
    );

    return { messages, sendMessage, isStreaming, error, isHistoryLoading };
}
