/**
 * Types for the side-by-side model comparison (POST /evaluation/compare).
 *
 * Mirrors backend/app/schemas/evaluation.py. Keep the two in step: the
 * response is rendered directly, so a field renamed on one side silently
 * becomes `undefined` on the other.
 */

export interface ComparisonScenario {
    source_ac_clause: string;
    feature: string;
    scenario: string;
    given: string;
    when: string;
    then: string;
}

export interface ProviderResult {
    configured_provider: string;
    /**
     * Which model actually answered. When this differs from
     * `configured_provider`, the fine-tuned endpoint failed and something else
     * answered — the output looks identical either way, so this field is the
     * only way to tell a real comparison from two runs of the same model.
     */
    effective_provider: string | null;
    fallback_reason: string | null;
    model_identifier: string;

    succeeded: boolean;
    error: string | null;
    scenarios: ComparisonScenario[];

    latency_seconds: number | null;
    /** null, never 0, when no AC clauses could be parsed or the model failed. */
    coverage: number | null;
    duplicate_rate: number | null;
    scenario_count: number;
}

export interface ComparisonResponse {
    acceptance_criteria: string;
    jira_ticket_id: string | null;
    ac_clauses: string[];
    results: ProviderResult[];
    /** False when a model did not answer, so there is nothing to compare. */
    comparable: boolean;
    notes: string[];
}

export interface ComparisonRequest {
    jira_ticket_id?: string;
    acceptance_criteria?: string;
    /**
     * Whose Jira to fetch the ticket from. Attached by the hook from the
     * active project — a request without it falls back to the server
     * environment and reports "Jira is not configured".
     */
    project_id?: string | null;
}

/* --- Saved runs -------------------------------------------------------- */

export interface BatchCompareRequest {
    jira_ticket_ids: string[];
    /** Groups the rows. Reusing an id adds to that run. */
    run_id: string;
    /**
     * Whose Jira to fetch the ticket from. Attached by the hook from the
     * active project — a request without it falls back to the server
     * environment and reports "Jira is not configured".
     */
    project_id?: string | null;
}

export interface BatchItemOutcome {
    jira_ticket_id: string;
    saved: boolean;
    comparable: boolean;
    ac_clause_count: number;
    error: string | null;
}

export interface BatchCompareResponse {
    run_id: string;
    requested: number;
    saved: number;
    items: BatchItemOutcome[];
}

export interface RunSummary {
    run_id: string;
    item_count: number;
    row_count: number;
    /** Rows answered by a model other than the one configured. */
    degraded_rows: number;
    first_created_at: string | null;
    last_created_at: string | null;
}

export interface ProviderMetrics {
    provider: string;
    items_scored: number;
    degraded_excluded: number;
    success_rate: number | null;
    coverage: number | null;
    duplicate_rate: number | null;
    reference_alignment: number | null;
    latency_seconds: number | null;
}

export interface GroupMetrics {
    domain_group: string;
    providers: ProviderMetrics[];
}

export interface RunReport {
    run_ids: string[];
    item_count: number;
    row_count: number;
    groups: GroupMetrics[];
    notes: string[];
}
