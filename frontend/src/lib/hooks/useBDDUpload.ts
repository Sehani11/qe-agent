"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";

export interface BDDUploadRequest {
    session_id: string;
    file: File;
}

export interface BDDUploadResponse {
    session_id: string;
    content: string;
    jira_ticket_id: string;
}

async function uploadBDD(request: BDDUploadRequest): Promise<BDDUploadResponse> {
    const formData = new FormData();
    formData.append("session_id", request.session_id);
    formData.append("file", request.file);

    const response = await apiClient.post<BDDUploadResponse>("/bdd/upload", formData, {
        headers: { "Content-Type": "multipart/form-data" },
    });
    return response.data;
}

export function useBDDUpload() {
    return useMutation({
        mutationFn: uploadBDD,
    });
}
