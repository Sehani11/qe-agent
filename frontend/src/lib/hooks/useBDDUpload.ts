"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { useTrainingOptIn } from "@/lib/hooks/useTrainingOptIn";
import { useActiveProjectId } from "@/lib/stores/projectStore";

export interface BDDUploadRequest {
    session_id: string;
    file: File;
}

export interface BDDUploadResponse {
    session_id: string;
    content: string;
    jira_ticket_id: string;
}

async function uploadBDD(
    request: BDDUploadRequest & {
        training_opt_in: boolean;
        project_id: string | null;
    }
): Promise<BDDUploadResponse> {
    const formData = new FormData();
    formData.append("session_id", request.session_id);
    formData.append("file", request.file);
    formData.append("training_opt_in", String(request.training_opt_in));
    // An upload can create the session, and sessions.project_id is NOT NULL.
    if (request.project_id) formData.append("project_id", request.project_id);

    const response = await apiClient.post<BDDUploadResponse>("/bdd/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data;
}

export function useBDDUpload() {
    // Uploaded rows are the dataset builder's default source — skipping this
    // path would leave the opt-out mostly cosmetic.
    const { requested: trainingOptIn } = useTrainingOptIn();
    const activeProjectId = useActiveProjectId();

    return useMutation({
        mutationFn: (request: BDDUploadRequest) =>
            uploadBDD({
                ...request,
                training_opt_in: trainingOptIn,
                project_id: activeProjectId,
            }),
    });
}
