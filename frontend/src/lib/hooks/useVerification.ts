"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import type { VerificationMode, VerificationFetchResponse } from "@/lib/types/verification";

interface VerificationFetchRequest {
    session_id: string;
    mode: VerificationMode;
    github_input: string;
}

async function fetchGitHubCode(
    request: VerificationFetchRequest
): Promise<VerificationFetchResponse> {
    const response = await apiClient.post<VerificationFetchResponse>(
        "/verification/fetch",
        request
    );
    return response.data;
}

export function useVerification() {
    return useMutation({
        mutationFn: fetchGitHubCode,
    });
}
