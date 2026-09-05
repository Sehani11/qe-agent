"use client";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

export const FINE_TUNED_STATUS_KEY = ["bdd", "fine-tuned-status"];

export interface FineTunedStatus {
    available: boolean;
    /** Name of the served model, when there is one. */
    model: string | null;
    /** Why it is unavailable, or what is being served. */
    detail: string;
}

/**
 * Whether a fine-tuned model is actually being served right now.
 *
 * The toggle that consumes this is a stored preference, and the generate path
 * falls back to the general LLM whenever the endpoint cannot answer — so a
 * switch reading "use fine-tuned model" can be on while every scenario comes
 * from somewhere else. This is what lets it say so instead.
 *
 * Refetched on focus and never held long: the model can go at any time. A wipe
 * removes it, and the serving shim is a process someone starts and stops by
 * hand, so a cached "available" is the exact thing this exists to prevent.
 */
export function useFineTunedStatus() {
    return useQuery<FineTunedStatus>({
        queryKey: FINE_TUNED_STATUS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<FineTunedStatus>(
                "/bdd/fine-tuned-status"
            );
            return data;
        },
        staleTime: 30_000,
        refetchOnWindowFocus: true,
    });
}
