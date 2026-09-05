"use client";

import { useMutation } from "@tanstack/react-query";

import apiClient from "@/lib/api/client";
import { filenameFromHeader, triggerBlobDownload } from "@/lib/download";

// ---------------------------------------------------------------------------
// Story 5.4: Traceability report export (PDF / CSV)
// ---------------------------------------------------------------------------

async function exportReport(
    sessionId: string,
    format: "pdf" | "csv"
): Promise<void> {
    const response = await apiClient.get(`/reports/${sessionId}/export/${format}`, {
        responseType: "blob",
    });
    const filename = filenameFromHeader(
        response.headers["content-disposition"],
        `${sessionId}_report.${format}`
    );
    triggerBlobDownload(response.data as Blob, filename);
}

/** Download the session's traceability report as a PDF. */
export function useExportReportPdf() {
    return useMutation({
        mutationFn: (sessionId: string) => exportReport(sessionId, "pdf"),
    });
}

/** Download the session's traceability report as a CSV. */
export function useExportReportCsv() {
    return useMutation({
        mutationFn: (sessionId: string) => exportReport(sessionId, "csv"),
    });
}
