"""Application configuration via Pydantic BaseSettings.

All required environment variables are declared here and validated at startup.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "QE Verification Agent API"
    app_version: str = "0.1.0"
    debug: bool = False

    # Dev stub — replaced by real JWT auth in Epic 2
    dev_user_id: str = "dev-stub"

    # Database (Supabase PostgreSQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/qe_agent"
    direct_database_url: str = ""  # used by Alembic only; falls back to database_url if unset
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # LLM Provider (general-purpose — used for verification, RAG Q&A, and BDD fallback)
    llm_provider: str = "claude"  # "claude", "openai", or "local"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"  # OpenAI model name
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"

    # BDD Model Provider (fine-tuned model vs general LLM for BDD generation)
    bdd_model_provider: str = "general_llm"  # "general_llm" or "fine_tuned"
    fine_tuned_model_endpoint: str = ""  # HTTP endpoint for fine-tuned model (Phase 2)
    fine_tuned_model_api_key: str = ""  # API key for fine-tuned model endpoint (Phase 2)

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_index_name: str = "qe-agent"

    # Jira
    jira_base_url: str = ""
    jira_api_token: str = ""
    jira_user_email: str = ""

    # GitHub
    github_access_token: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str = ""

    # Supabase Storage — one shared bucket; per-feature data is namespaced by
    # subfolder inside it (reports/, feature-files/, artifacts/). Create this
    # single bucket in Supabase Studio → Storage → New Bucket (private).
    supabase_bucket: str = "qe-agent"

    # Confluence (for project knowledge base ingestion)
    confluence_base_url: str = ""
    confluence_api_token: str = ""
    confluence_user_email: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS — comma-separated list of allowed origins
    cors_allow_origins: str = "http://localhost:3000"

    # OpenAPI docs
    disable_docs: bool = False

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = Settings()
