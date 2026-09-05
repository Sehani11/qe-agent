export interface ConfluenceIngestRequest {
    space_key?: string;
    /** Page URLs and/or bare numeric ids. Takes precedence over the fields below. */
    page_refs?: string[];
    /** Predates `page_refs`; kept so existing callers keep working. */
    page_id?: string;
}

export interface JiraIngestRequest {
    /** Required unless `ticket_refs` is given — the backend rejects neither. */
    project_key?: string;
    sprint?: string;
    label?: string;
    /** Issue URLs and/or bare keys. Takes precedence over `project_key`. */
    ticket_refs?: string[];
}

export interface KnowledgeSource {
    id: string;
    user_id: string;
    source_type: string;
    source_url: string | null;
    title: string | null;
    page_count: number;
    ingestion_status: string;
    created_at: string;
}

export interface KnowledgeSSEEvent {
    type: "progress" | "complete" | "error";
    message?: string;
    current?: number;
    total?: number;
    ingested_count?: number;
    error?: string;
    // Code indexing reports what it built rather than a document count. The
    // fields are optional on the shared type because one stream carries one
    // shape or the other, never both.
    /** Source files embedded into the index. */
    indexed_files?: number;
    /** Chunks written across those files. */
    chunks?: number;
    /** The commit the index was built at. */
    sha?: string;
}

/** Request body for POST /api/v1/knowledge/index/code. */
export interface CodeIndexRequest {
    repo_url: string;
    ref?: string;
}

/** Response from POST /api/v1/knowledge/ingest/document (Story 4.6). */
export interface DocumentIngestResponse {
    ingested_count: number;
    chunk_count: number;
    title: string;
}
