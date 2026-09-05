"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import apiClient from "@/lib/api/client";
import { useActiveProjectId } from "@/lib/stores/projectStore";
import type {
    BatchCompareRequest,
    BatchCompareResponse,
    ComparisonRequest,
    ComparisonResponse,
    RunReport,
    RunSummary,
} from "@/lib/types/evaluation";

const RUNS_KEY = ["evaluation", "runs"];
const REPORT_KEY = ["evaluation", "report"];

/**
 * A generous client timeout, well beyond the backend's own budget.
 *
 * The endpoint runs BOTH models in sequence. The general LLM answers in a few
 * seconds; the fine-tuned model runs partly on the CPU and has been measured
 * between 9s and about three minutes depending on how many AC clauses the
 * input has. A default axios timeout would abort a request the server was
 * still working on, and the user would read that as the fine-tune failing when
 * it had not.
 */
const COMPARE_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Run one set of acceptance criteria through both models and score both.
 *
 * Send `jira_ticket_id` OR `acceptance_criteria`; the backend rejects a body
 * with neither. There is deliberately no fallback on the server for this call,
 * so a failed model returns an empty column rather than the other model's
 * output under the wrong label.
 */
export function useModelComparison() {
    const activeProjectId = useActiveProjectId();

    return useMutation<ComparisonResponse, Error, ComparisonRequest>({
        mutationFn: async (payload: ComparisonRequest) => {
            const { data } = await apiClient.post<ComparisonResponse>(
                "/evaluation/compare",
                // Attached here rather than by each caller: a ticket is fetched
                // from the ACTIVE project's Jira, and a caller that forgot the
                // id would silently fall back to the server environment and
                // report "Jira is not configured".
                { project_id: activeProjectId, ...payload },
                { timeout: COMPARE_TIMEOUT_MS }
            );
            return data;
        },
        retry: false, // each attempt can cost minutes; never repeat silently
    });
}

/**
 * Compare several tickets in one go and SAVE the results under a run id.
 *
 * The timeout scales with the number of tickets: each one runs both models in
 * sequence, so ten tickets can legitimately take far longer than a single
 * comparison. A fixed timeout would abort a run the server was still writing
 * rows for — and those rows would then be orphaned but real.
 */
export function useBatchComparison() {
    const queryClient = useQueryClient();
    const activeProjectId = useActiveProjectId();

    return useMutation<BatchCompareResponse, Error, BatchCompareRequest>({
        mutationFn: async (payload: BatchCompareRequest) => {
            const { data } = await apiClient.post<BatchCompareResponse>(
                "/evaluation/runs",
                // Same reason as the single comparison above.
                { project_id: activeProjectId, ...payload },
                { timeout: COMPARE_TIMEOUT_MS * Math.max(1, payload.jira_ticket_ids.length) }
            );
            return data;
        },
        retry: false,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            queryClient.invalidateQueries({ queryKey: REPORT_KEY });
        },
    });
}

/** Saved runs for the current user, newest first. */
export function useEvaluationRuns() {
    return useQuery({
        queryKey: RUNS_KEY,
        queryFn: async () => {
            const { data } = await apiClient.get<RunSummary[]>("/evaluation/runs");
            return data;
        },
        retry: false,
    });
}

/**
 * Aggregated metrics. Pass a run id for one run, or omit it to pool every run
 * — the report across several sessions.
 */
export function useEvaluationReport(runId?: string) {
    return useQuery({
        queryKey: [...REPORT_KEY, runId ?? "all"],
        queryFn: async () => {
            const { data } = await apiClient.get<RunReport>("/evaluation/report", {
                params: runId ? { run_id: runId } : undefined,
            });
            return data;
        },
        retry: false,
        // 404 simply means nothing has been saved yet, which the UI renders as
        // an empty state rather than an error.
        throwOnError: false,
    });
}

/** Delete every saved row for one run. */
export function useDeleteRun() {
    const queryClient = useQueryClient();
    return useMutation<void, Error, string>({
        mutationFn: async (runId: string) => {
            await apiClient.delete(`/evaluation/runs/${encodeURIComponent(runId)}`);
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: RUNS_KEY });
            queryClient.invalidateQueries({ queryKey: REPORT_KEY });
        },
    });
}
