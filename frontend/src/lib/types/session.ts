export type SSEEventType = "log" | "complete" | "error";

export interface SSELogEvent {
    type: "log";
    message: string;
    timestamp: string;
}

export interface SSECompleteEvent {
    type: "complete";
    session_id: string;
    acceptance_criteria?: string;
}

export interface SSEErrorEvent {
    type: "error";
    message: string;
    code: string;
}

export type PipelineSSEEvent = SSELogEvent | SSECompleteEvent | SSEErrorEvent;

export interface SessionState {
    sessionId: string | null;
    jiraTicketId: string | null;
    acceptanceCriteria: string | null;
    bddContent: string;
    logs: SSELogEvent[];
    isIngesting: boolean;
    isGeneratingBDD: boolean;
    isVerifying: boolean;
    error: string | null;
}

// ---------------------------------------------------------------------------
// Story 3.5: Session history API response types (snake_case matches backend JSON)
// ---------------------------------------------------------------------------

/** A session row returned by GET /api/v1/sessions */
export interface SessionListItem {
    id: string;
    user_id: string;
    jira_ticket_id: string;
    created_at: string;   // ISO 8601 UTC string
    bdd_status: "none" | "generated" | "uploaded";
}

/** Response from GET /api/v1/sessions/{id}/bdd */
export interface SessionBDDResponse {
    session_id: string;
    content: string;   // JSON string when source="generated"; raw Gherkin when source="uploaded"
    source: "generated" | "uploaded";
    created_at: string;
}

/** A stored verification result row from GET /api/v1/sessions/{id}/verification-results */
export interface StoredVerificationResult {
    id: string;
    session_id: string;
    scenario_id: string;
    scenario_title: string;
    status: "pass" | "fail";
    justification: string;
    code_reference: Record<string, unknown>;
    github_links: unknown[];
    implementation_suggestion: string | null;
    created_at: string;
}
