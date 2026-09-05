import type { RagContextItem, VerificationMode } from "@/lib/types/verification";

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
    /** The URL or key submitted at ingestion; null for sessions that predate it
     *  and for sessions created by uploading a .feature file. */
    jira_ticket_url: string | null;
    created_at: string;   // ISO 8601 UTC string
    bdd_status: "none" | "generated" | "uploaded" | "edited";
    /** Whether any verification verdict has been recorded for this session. */
    verification_status: "none" | "completed";
}

/** One page of sessions from GET /api/v1/sessions?limit=&offset= */
export interface SessionListPage {
    items: SessionListItem[];
    /** Count of ALL the user's sessions, not this page — sizes the pager. */
    total: number;
    limit: number;
    offset: number;
}

/** Response from GET /api/v1/sessions/{id}/bdd */
export interface SessionBDDResponse {
    session_id: string;
    content: string;   // JSON string when source="generated"; raw Gherkin when "uploaded"/"edited"
    source: "generated" | "uploaded" | "edited";
    created_at: string;
}

/** A stored verification result row from GET /api/v1/sessions/{id}/verification-results */
export interface StoredVerificationResult {
    id: string;
    session_id: string;
    scenario_id: string;
    scenario_title: string;
    status: "pass" | "partial" | "fail" | "inconclusive";
    justification: string;
    code_reference: Record<string, unknown>;
    github_links: unknown[];
    implementation_suggestion: string | null;
    // Story 4.4 — persisted RAG context, rendered on session revisit.
    rag_context?: RagContextItem[] | null;
    // The source this verdict was checked against. Null on rows written before
    // these columns existed, so every consumer must tolerate their absence.
    verification_mode?: VerificationMode | null;
    github_input?: string | null;
    created_at: string;
}
