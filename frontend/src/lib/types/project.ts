/** Project types — mirrors `app/schemas/project.py`. */

/**
 * A project as the API returns it.
 *
 * Note what is NOT here: no token fields. Credentials are write-only —
 * the server reports whether one is set and never what it is, so there is no
 * shape in which a secret could reach this client.
 */
export interface Project {
    id: string;
    name: string;

    jira_base_url: string;
    jira_user_email: string;
    has_jira_token: boolean;

    confluence_base_url: string;
    confluence_user_email: string;
    has_confluence_token: boolean;

    github_repo: string;
    has_github_token: boolean;

    /** Default model for this project's actions. Empty means "no preference". */
    llm_provider: string;
    llm_model: string;

    /**
     * Which vendor embeds this project's knowledge base ("openai" | "voyage"),
     * and the index those vectors live in. Empty means "use the server's".
     *
     * They are one setting in two halves: an index holds vectors from exactly
     * one embedding model, so changing the vendor without pointing at a new
     * index of the matching dimension is rejected by Pinecone.
     */
    embedding_provider: string;
    pinecone_index_name: string;

    created_at: string;
    updated_at: string;
}

/** A row in the switcher. */
export interface ProjectSummary {
    id: string;
    name: string;
    created_at: string;
}

/**
 * A config update. Every field optional so one section can be saved alone.
 *
 * Token fields carry three states the server distinguishes:
 *   omitted -> keep what is stored
 *   ""      -> clear it
 *   text    -> replace it
 * So a form must send `undefined`, not `""`, for a token the user did not touch.
 */
export interface ProjectConfigUpdate {
    name?: string;

    jira_base_url?: string;
    jira_user_email?: string;
    jira_api_token?: string;

    confluence_base_url?: string;
    confluence_user_email?: string;
    confluence_api_token?: string;

    github_repo?: string;
    github_access_token?: string;

    llm_provider?: string;
    llm_model?: string;

    embedding_provider?: string;
    pinecone_index_name?: string;
}
