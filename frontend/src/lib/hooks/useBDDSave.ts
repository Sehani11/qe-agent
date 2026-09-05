"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { useTrainingOptIn } from "@/lib/hooks/useTrainingOptIn";

export interface BDDSaveRequest {
    session_id: string;
    content: string;
}

export interface BDDSaveResponse {
    id: string;
    session_id: string;
    source: string;
    created_at: string;
}

/**
 * Persist edited BDD content (Story 6.4).
 *
 * Sends only the session and the content — the generated row this edit derives
 * from is resolved server-side, so nothing here needs to track row ids.
 */
async function saveBDD(
    request: BDDSaveRequest & { training_opt_in: boolean }
): Promise<BDDSaveResponse> {
    const response = await apiClient.post<BDDSaveResponse>("/bdd/save", request);
    return response.data;
}

export function useBDDSave() {
    // A human correcting generated output is the strongest training signal
    // there is, so this path must honour the opt-out as much as generation.
    const { requested: trainingOptIn } = useTrainingOptIn();

    return useMutation({
        mutationFn: (request: BDDSaveRequest) =>
            saveBDD({ ...request, training_opt_in: trainingOptIn }),
    });
}
