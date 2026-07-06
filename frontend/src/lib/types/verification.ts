// Verification types for Epic 2 — GitHub Code Verification
// No auth required; uses DEV_USER_ID stub from Epic 1

export type VerificationMode = 'exact_files' | 'full_repo' | 'pull_request';

export interface GitHubSourceInput {
    mode: VerificationMode;
    value: string;
}

/** A single file fetched from GitHub (content or diff patch). */
export interface FetchedFile {
    path: string;
    content: string;
    github_url?: string | null;
}

/** Response from POST /api/v1/verification/fetch */
export interface VerificationFetchResponse {
    session_id: string;
    mode: string;
    fetched_files: FetchedFile[];
}

// ---------------------------------------------------------------------------
// Story 2.3: LLM Verification types
// ---------------------------------------------------------------------------

/** Code location used as evidence in a verdict. */
export interface CodeReference {
    file: string;
    function: string;
    line: number;
}

/** Per-scenario verdict returned by the LLM via SSE. */
export interface VerificationVerdict {
    scenario_id: string;
    scenario_title: string;
    status: 'pass' | 'fail';
    justification: string;
    code_reference: CodeReference;
    github_links: string[];
    implementation_suggestion: string | null;
}

/** Request payload for POST /api/v1/verification/run */
export interface VerificationRunRequest {
    session_id: string;
    bdd_content: string;
    fetched_files: FetchedFile[];
}

/** Summary emitted after all scenarios are evaluated. */
export interface VerificationSummary {
    total: number;
    passed: number;
    failed: number;
}

// ---------------------------------------------------------------------------
// Story 2.5: Agentic verification types
// ---------------------------------------------------------------------------

/** Request payload for POST /api/v1/verification/run-agentic */
export interface AgenticVerificationRequest {
    session_id: string;
    bdd_content: string;
    mode: VerificationMode;
    github_input: string;
}
