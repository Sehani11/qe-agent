"use client";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import type {
    SessionBDDResponse,
    SessionListItem,
    StoredVerificationResult,
} from "@/lib/types/session";

/** Fetch the authenticated user's session list from GET /api/v1/sessions. */
export function useSessionList() {
    return useQuery({
        queryKey: ["sessions"],
        queryFn: async () => {
            const { data } = await apiClient.get<SessionListItem[]>("/sessions");
            return data;
        },
    });
}

/** Fetch a single session by ID from GET /api/v1/sessions/{id}. */
export function useSession(sessionId: string | null) {
    return useQuery({
        queryKey: ["sessions", sessionId],
        queryFn: async () => {
            const { data } = await apiClient.get<SessionListItem>(
                `/sessions/${sessionId}`
            );
            return data;
        },
        enabled: !!sessionId,
        retry: false,
    });
}

/**
 * Fetch the most recent BDD content for an existing session.
 * Returns undefined while disabled; throws on non-404 errors.
 * A 404 response (no BDD yet) is treated as a normal "not found" — caller should
 * check isError and error.response?.status to distinguish.
 */
export function useSessionBDD(sessionId: string | null) {
    return useQuery({
        queryKey: ["sessions", sessionId, "bdd"],
        queryFn: async () => {
            const { data } = await apiClient.get<SessionBDDResponse>(
                `/sessions/${sessionId}/bdd`
            );
            return data;
        },
        enabled: !!sessionId,
        retry: false, // 404 = no BDD yet; don't retry
    });
}

/** Fetch stored verification results for a session from GET /api/v1/sessions/{id}/verification-results. */
export function useSessionVerificationResults(sessionId: string | null) {
    return useQuery({
        queryKey: ["sessions", sessionId, "verification-results"],
        queryFn: async () => {
            const { data } = await apiClient.get<StoredVerificationResult[]>(
                `/sessions/${sessionId}/verification-results`
            );
            return data;
        },
        enabled: !!sessionId,
        retry: false,
    });
}
