// Verification types for Epic 2 — GitHub Code Verification
// No auth required; uses DEV_USER_ID stub from Epic 1

export type VerificationMode = 'exact_files' | 'full_repo' | 'pull_request';

/**
 * Preselected so the panel opens ready to run. Full repo is the safe default:
 * it needs only the repository URL, and it is the only mode that can find
 * code the user did not already know to point at. The narrower modes are
 * optimisations you opt into once you know where the behaviour lives.
 */
export const DEFAULT_VERIFICATION_MODE: VerificationMode = 'full_repo';

// ---------------------------------------------------------------------------
// Story 2.3: LLM Verification types
// ---------------------------------------------------------------------------

/** Code location used as evidence in a verdict. */
export interface CodeReference {
    file: string;
    function: string;
    line: number;
}

// ---------------------------------------------------------------------------
// Story 4.3: RAG-Enriched Verification types
// ---------------------------------------------------------------------------

/** A single knowledge base chunk retrieved during RAG enrichment. */
export interface RagContextItem {
    source: string;      // "confluence" | "jira"
    source_id: string;   // Confluence page ID or Jira ticket key
    snippet: string;     // Excerpt of the retrieved chunk, <=300 chars
    // Story 4.4 — optional; absent for chunks ingested before 4.4.
    title?: string;      // Confluence page title / Jira summary
    url?: string;        // Deep link to the source; empty when not linkable
}

/** Per-scenario verdict returned by the LLM via SSE. */
export interface VerificationVerdict {
    scenario_id: string;
    scenario_title: string;
    /**
     * 'inconclusive' is not a softer 'fail'. It means the run could not gather
     * evidence either way, so the scenario needs another verification pass —
     * not a developer sent to write code that may already exist.
     *
     * 'partial' is the opposite shape: the behaviour was found in the code but
     * is not reachable the way the scenario describes — wired to a different
     * trigger, or built on one side of the stack only. Reporting those as
     * 'fail' sends someone to rebuild working code.
     */
    status: 'pass' | 'partial' | 'fail' | 'inconclusive';
    justification: string;
    code_reference: CodeReference;
    github_links: string[];
    implementation_suggestion: string | null;
    rag_context?: RagContextItem[] | null;
}

/**
 * Emitted once before the first verdict, describing the work about to be done.
 *
 * `total` is what will actually be verified, which is lower than the number of
 * scenarios in the BDD file whenever duplicates were dropped. Progress measured
 * against the file would stall short of 100% in that case.
 */
export interface VerificationPlan {
    total: number;
    duplicates_skipped: number;
    max_concurrency: number;
}

/** Summary emitted after all scenarios are evaluated. */
export interface VerificationSummary {
    total: number;
    passed: number;
    failed: number;
    /** Optional: absent on summaries computed before inconclusive existed. */
    inconclusive?: number;
    /** Optional: absent on summaries computed before partial existed. */
    partial?: number;
}

// ---------------------------------------------------------------------------
// Story 2.5: Agentic verification types
// ---------------------------------------------------------------------------

/** Request payload for POST /api/v1/verification/run-agentic */
export interface AgenticVerificationRequest {
    session_id: string;
    /** Whose GitHub token reads the repository. Null falls back to the server. */
    project_id: string | null;
    bdd_content: string;
    mode: VerificationMode;
    github_input: string;
    /** Opt-in: enrich each scenario's prompt with project knowledge-base context. */
    use_knowledge_base: boolean;
    /**
     * Opt-in: let semantic search over the project's indexed code suggest which
     * files each scenario is likely implemented in. The suggestions tell the
     * agent where to start reading — they are never evidence for a verdict, and
     * are ignored server-side when the project has no index or its index is
     * stale.
     */
    code_index_enabled: boolean;
}

/** Response from GET /api/v1/knowledge/index/code. */
export interface CodeIndexStatus {
    indexed: boolean;
    repo: string | null;
    /** Commit SHA the index was built at — what makes "indexed at" a fact. */
    indexed_ref: string | null;
    file_count: number;
    indexed_at: string | null;
}
