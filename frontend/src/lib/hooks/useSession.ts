"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import type {
    SessionBDDResponse,
    SessionListItem,
    SessionListPage,
    StoredVerificationResult,
} from "@/lib/types/session";

/** Sessions per page. Mirrors the API's own default page size. */
export const SESSIONS_PAGE_SIZE = 20;

/**
 * Fetch one page of the user's sessions from GET /api/v1/sessions.
 *
 * Keeps the previous page's rows on screen while the next one loads, so paging
 * does not collapse the list back to a skeleton on every click.
 */
export function useSessionList(
    { limit = SESSIONS_PAGE_SIZE, offset = 0 }: { limit?: number; offset?: number } = {}
) {
    const activeProjectId = useActiveProjectId();

    return useQuery({
        // The project is part of the key, so switching refetches rather than
        // serving the previous project's rows out of cache.
        queryKey: ["sessions", "list", { limit, offset, activeProjectId }],
        queryFn: async () => {
            const { data } = await apiClient.get<SessionListPage>("/sessions", {
                params: { limit, offset, project_id: activeProjectId },
            });
            return data;
        },
        placeholderData: keepPreviousData,
        // Nothing to ask for until a project is resolved; requesting without one
        // would list every project's sessions for a frame before correcting.
        enabled: activeProjectId !== null,
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

/**
 * Delete a session and everything scoped to it (BDD content, verification
 * results, chat history) via DELETE /api/v1/sessions/{id}.
 */
export function useDeleteSession() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (sessionId: string) => {
            await apiClient.delete(`/sessions/${sessionId}`);
            return sessionId;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ["sessions"] });
        },
    });
}

/**
 * Delete a hand-picked set of sessions (the multi-select checkboxes).
 *
 * There is no bulk-by-id endpoint — selections are page-sized (at most
 * SESSIONS_PAGE_SIZE), so one request per id in parallel costs one round trip
 * of latency, not one per session. `allSettled` so one failing delete does not
 * stop the rest from going through; the caller is told how many of each.
 */
export function useDeleteSessions() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (sessionIds: string[]) => {
            const results = await Promise.allSettled(
                sessionIds.map((id) => apiClient.delete(`/sessions/${id}`))
            );
            const succeeded = results.filter((r) => r.status === "fulfilled").length;
            const failed = results.length - succeeded;
            return { succeeded, failed };
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ["sessions"] });
        },
    });
}

/**
 * Delete every session in the active project via DELETE /api/v1/sessions.
 *
 * One request rather than looping the per-session delete — the project can
 * hold far more sessions than fit on one page, so this must not cost one
 * round trip per row. Resolves to the number of sessions removed.
 */
export function useDeleteAllSessions() {
    const activeProjectId = useActiveProjectId();
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: async () => {
            const { data } = await apiClient.delete<{ deleted: number }>("/sessions", {
                params: { project_id: activeProjectId },
            });
            return data.deleted;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ["sessions"] });
        },
    });
}
