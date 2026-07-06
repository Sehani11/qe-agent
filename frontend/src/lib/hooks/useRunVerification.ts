"use client";

import { useCallback } from "react";
import { useSessionContext } from "@/context/SessionContext";
import { supabase } from "@/lib/supabase/client";
import { useSSEStream } from "./useSSEStream";
import type { AgenticVerificationRequest, VerificationVerdict } from "../types/verification";

/**
 * Hook for running agentic LLM verification via POST /api/v1/verification/run-agentic SSE endpoint.
 *
 * The agentic flow requires no pre-fetch step — the backend LLM calls GitHub tools
 * on demand. The payload is AgenticVerificationRequest (session_id, bdd_content, mode, github_input).
 *
 * isVerifying is derived from useSSEStream.isStreaming — the single source of truth
 * for loading state per the Story 2.2 code review mandate.
 */
export function useRunVerification() {
    const {
        setVerificationResults,
        setVerificationSummary,
        setGlobalError,
        setIsVerifying,
    } = useSessionContext();

    const { connect, isStreaming } = useSSEStream({
        onStatusChange: useCallback((streaming: boolean) => {
            setIsVerifying(streaming);
        }, [setIsVerifying]),
        onVerdict: useCallback(
            (verdict: VerificationVerdict) => {
                setVerificationResults((prev) => [...prev, verdict]);
            },
            [setVerificationResults],
        ),
        onVerificationComplete: useCallback(
            (total: number, passed: number, failed: number) => {
                setVerificationSummary({ total, passed, failed });
            },
            [setVerificationSummary],
        ),
        onError: useCallback(
            (message: string) => {
                // Do NOT clear bddContent, verificationMode, githubInput, sessionId on error
                setGlobalError(message);
            },
            [setGlobalError],
        ),
    });

    const runVerification = useCallback(
        async (payload: AgenticVerificationRequest) => {
            // Clear stale results from any previous run before starting a new stream
            setVerificationResults([]);
            setVerificationSummary(null);

            // Raw fetch needs the full backend URL (not Next.js origin) and the
            // Bearer token — apiClient handles both automatically, but useSSEStream
            // uses fetch() directly.
            const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
            const { data: { session } } = await supabase.auth.getSession();
            const authHeaders: Record<string, string> = session?.access_token
                ? { Authorization: `Bearer ${session.access_token}` }
                : {};

            connect(
                `${apiBaseUrl}/api/v1/verification/run-agentic`,
                "POST",
                payload as unknown as Record<string, unknown>,
                authHeaders,
            );
        },
        [connect, setVerificationResults, setVerificationSummary],
    );

    return { runVerification, isVerifying: isStreaming  };
}
