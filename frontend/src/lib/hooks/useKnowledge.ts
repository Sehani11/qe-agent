"use client";

import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import { supabase } from "@/lib/supabase/client";
import type {
    CodeIndexRequest,
    ConfluenceIngestRequest,
    DocumentIngestResponse,
    JiraIngestRequest,
    KnowledgeSource,
    KnowledgeSSEEvent,
} from "@/lib/types/knowledge";
import type { CodeIndexStatus } from "@/lib/types/verification";

interface UseIngestDocumentOptions {
    onComplete?: (result: DocumentIngestResponse) => void;
    onError?: (message: string) => void;
}

/**
 * Upload a PDF/DOCX document to the knowledge base (Story 4.6).
 * Multipart POST via the shared apiClient (JWT interceptor applies).
 * Unlike the SSE ingest hooks, this is a normal request/response.
 */
export function useIngestDocument(options?: UseIngestDocumentOptions) {
    const activeProjectId = useActiveProjectId();
    const [isUploading, setIsUploading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<DocumentIngestResponse | null>(null);

    const ingest = useCallback(
        async (file: File) => {
            setIsUploading(true);
            setError(null);
            setResult(null);
            try {
                const form = new FormData();
                form.append("file", file);
                // Embeds into this project's namespace; without it the document
                // lands in the pre-projects one and nothing here can find it.
                if (activeProjectId) form.append("project_id", activeProjectId);
                const { data } = await apiClient.post<DocumentIngestResponse>(
                    "/knowledge/ingest/document",
                    form,
                    { headers: { "Content-Type": "multipart/form-data" } }
                );
                setResult(data);
                options?.onComplete?.(data);
            } catch (err: unknown) {
                let message = "Upload failed.";
                if (typeof err === "object" && err !== null && "response" in err) {
                    const axiosErr = err as { response?: { data?: { message?: string } } };
                    message = axiosErr.response?.data?.message ?? message;
                } else if (err instanceof Error) {
                    message = err.message;
                }
                setError(message);
                options?.onError?.(message);
            } finally {
                setIsUploading(false);
            }
        },
        [options, activeProjectId]
    );

    const reset = useCallback(() => {
        setError(null);
        setResult(null);
    }, []);

    return { ingest, isUploading, error, result, reset };
}

/**
 * Fetch the authenticated user's ingested knowledge sources from
 * GET /api/v1/knowledge/sources. Uses the shared apiClient (JWT interceptor)
 * — unlike the ingest hooks, which need raw fetch for SSE streaming.
 */
export function useKnowledgeSources() {
    const activeProjectId = useActiveProjectId();

    return useQuery({
        // The project is in the key: a switch must refetch, not serve the
        // previous project's sources from cache.
        queryKey: ["knowledge", "sources", activeProjectId],
        queryFn: async () => {
            const { data } = await apiClient.get<KnowledgeSource[]>(
                "/knowledge/sources",
                { params: { project_id: activeProjectId } }
            );
            return data;
        },
        retry: false, // consistent with useSession hooks; don't hammer on 401
    });
}

/**
 * Delete an ingested knowledge source (Story 4.7). Removes the source's vectors
 * and DB row via DELETE /knowledge/sources/{id}, then invalidates the sources
 * list so the row disappears from the UI.
 */
export function useDeleteKnowledgeSource() {
    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async (id: string) => {
            await apiClient.delete(`/knowledge/sources/${id}`);
            return id;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ["knowledge", "sources"] });
        },
    });
}

/**
 * Delete every knowledge source the caller owns.
 *
 * One request, not a loop of per-source deletes: the backend wipes the user's
 * whole vector namespace in a single call, which also clears vectors no
 * per-source delete could target (legacy rows with no source_ref, orphans from
 * an earlier failure). Resolves to the number of rows removed.
 */
export function useDeleteAllKnowledgeSources() {
    const activeProjectId = useActiveProjectId();

    const queryClient = useQueryClient();
    return useMutation({
        mutationFn: async () => {
            const { data } = await apiClient.delete<{ deleted: number }>(
                "/knowledge/sources",
                { params: { project_id: activeProjectId } }
            );
            return data.deleted;
        },
        onSuccess: () => {
            void queryClient.invalidateQueries({ queryKey: ["knowledge", "sources"] });
        },
    });
}

interface UseIngestOptions {
    onProgress?: (event: KnowledgeSSEEvent) => void;
    /**
     * `event` carries whatever else the stream reported on completion — code
     * indexing reports files, chunks and the commit it built at, none of which
     * fit `ingestedCount`. Callers that only need the count ignore it.
     */
    onComplete?: (ingestedCount: number, event: KnowledgeSSEEvent) => void;
    onError?: (message: string) => void;
}

interface UseIngestResult {
    isIngesting: boolean;
    error: string | null;
    progressMessage: string | null;
}

const API_BASE_URL =
    process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function useSSEIngest<T>(
    endpoint: string,
    options?: UseIngestOptions
): UseIngestResult & { ingest: (request: T) => Promise<void> } {
    const [isIngesting, setIsIngesting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [progressMessage, setProgressMessage] = useState<string | null>(null);
    // Added here rather than at each call site so no ingestion path can be
    // added later that quietly falls back to the server's own credentials.
    const activeProjectId = useActiveProjectId();

    const ingest = useCallback(
        async (request: T) => {
            setIsIngesting(true);
            setError(null);
            setProgressMessage(null);

            try {
                const {
                    data: { session },
                } = await supabase.auth.getSession();
                // Typed as Record<string, string> rather than inferred. The
                // ternary otherwise infers a union with `{}`, and spreading
                // that into a headers object widens it to something TypeScript
                // will not accept as HeadersInit — which fails `next build`
                // even though the code is correct at runtime.
                const authHeader: Record<string, string> = session?.access_token
                    ? { Authorization: `Bearer ${session.access_token}` }
                    : {};

                const response = await fetch(
                    `${API_BASE_URL}${endpoint}`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Accept: "text/event-stream",
                            ...authHeader,
                        },
                        body: JSON.stringify({
                            ...request,
                            project_id: activeProjectId,
                        }),
                    }
                );

                if (!response.ok || !response.body) {
                    throw new Error(
                        `Request failed with status: ${response.status}`
                    );
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder("utf-8");
                let buffer = "";

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split("\n\n");
                    buffer = lines.pop() ?? "";

                    for (const line of lines) {
                        if (!line.startsWith("data: ")) continue;
                        const raw = line.slice(6).trim();
                        if (!raw) continue;

                        try {
                            const event = JSON.parse(raw) as KnowledgeSSEEvent;

                            if (event.type === "progress") {
                                setProgressMessage(event.message ?? null);
                                options?.onProgress?.(event);
                            } else if (event.type === "complete") {
                                options?.onComplete?.(
                                    event.ingested_count ?? 0,
                                    event
                                );
                            } else if (event.type === "error") {
                                const msg =
                                    event.message ?? "Ingestion failed.";
                                setError(msg);
                                options?.onError?.(msg);
                            }
                        } catch {
                            // Ignore malformed SSE frames
                        }
                    }
                }
            } catch (err: unknown) {
                const msg =
                    err instanceof Error
                        ? err.message
                        : "Failed to connect to server.";
                setError(msg);
                options?.onError?.(msg);
            } finally {
                setIsIngesting(false);
            }
        },
        [endpoint, options, activeProjectId]
    );

    return { ingest, isIngesting, error, progressMessage };
}

export function useIngestConfluence(options?: UseIngestOptions) {
    return useSSEIngest<ConfluenceIngestRequest>(
        "/api/v1/knowledge/ingest/confluence",
        options
    );
}

export function useIngestJira(options?: UseIngestOptions) {
    return useSSEIngest<JiraIngestRequest>(
        "/api/v1/knowledge/ingest/jira",
        options
    );
}

/** Cache key for one project's code-index status. */
export const codeIndexKey = (projectId: string | null) =>
    ["knowledge", "code-index", projectId] as const;

/**
 * Whether this project has an indexed copy of its repository, and what commit
 * it was built at.
 *
 * The verification form reads this to decide whether its code-index toggle can
 * be enabled at all — offering a switch that silently does nothing is worse
 * than not offering it.
 */
export function useCodeIndexStatus() {
    const activeProjectId = useActiveProjectId();

    return useQuery({
        // The project is in the key for the same reason it is in the sources
        // key: an index belongs to one project's namespace, so a switch must
        // refetch rather than show the previous project's status.
        queryKey: codeIndexKey(activeProjectId),
        queryFn: async () => {
            const { data } = await apiClient.get<CodeIndexStatus>(
                "/knowledge/index/code",
                { params: { project_id: activeProjectId } }
            );
            return data;
        },
        retry: false,
    });
}

/**
 * Build or rebuild this project's code index, streaming SSE progress.
 *
 * Invalidates the status query when the stream completes, so the "indexed at"
 * line and the verification toggle reflect the new index without a reload.
 */
export function useIndexCode(options?: UseIngestOptions) {
    const queryClient = useQueryClient();
    const activeProjectId = useActiveProjectId();

    const onComplete = useCallback(
        (count: number, event: KnowledgeSSEEvent) => {
            void queryClient.invalidateQueries({
                queryKey: codeIndexKey(activeProjectId),
            });
            options?.onComplete?.(count, event);
        },
        [queryClient, activeProjectId, options]
    );

    return useSSEIngest<CodeIndexRequest>("/api/v1/knowledge/index/code", {
        ...options,
        onComplete,
    });
}
