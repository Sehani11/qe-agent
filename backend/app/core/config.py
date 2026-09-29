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
    # These are the DEFAULTS a call falls back to. A request that carries an
    # explicit provider/model (the frontend model picker) overrides them per
    # call; anything without request context — the evaluation CLI, background
    # jobs — lands here.
    llm_provider: str = "openai"  # "claude", "openai", or "local"/"ollama"
    llm_model: str = "gpt-4o"  # OpenAI model name

    # How many scenarios one verification run may have in flight at once.
    #
    # This is the run's rate-limit dial, and it is the one that matters. Every
    # in-flight scenario sends the repository evidence at the same moment, and
    # the evidence caps were raised roughly eightfold to fix a truncation bug
    # that made the agent report working code as missing. Those caps are an
    # accuracy guarantee and are not the thing to trade away; the number of
    # requests in flight is.
    #
    # 2 rather than 5: verification used to be strictly sequential, and five
    # concurrent scenarios carrying the larger evidence blocks put roughly an
    # order of magnitude more tokens per minute through one account than that
    # ever did — enough to sit on a provider's TPM ceiling for a whole run.
    # Two keeps most of the wall-clock saving at a fraction of the peak.
    # Set 1 for the fully sequential behaviour, or raise it on a high-tier key.
    verification_max_concurrency: int = 2

    # How many of the most recent tool results stay verbatim in the agent's
    # replayed conversation; 0 keeps all of them.
    #
    # Every round of a tool loop re-sends every earlier round, and tool results
    # are nearly all of that: one file read can be 10 000 tokens, paid for again
    # on each subsequent call. A window replaces the older ones with a
    # placeholder that says the content is still retrievable, which is the only
    # lever that lowers the tokens actually sent — prompt caching lowers the
    # bill but not the per-minute token count a 429 is measured against.
    #
    # Default 0 (off) because it trades an accuracy risk for that saving: a
    # model that can no longer see a file it read might report the behaviour it
    # was looking for as missing. Before raising this, run the same BDD with it
    # off and on and confirm no verdict changed — especially that a genuinely
    # missing feature still comes back "fail" and not "inconclusive". 2 is the
    # value to try first.
    verification_tool_result_window: int = 0

    # Render the repository file tree grouped by directory instead of one full
    # path per line. 34-46% smaller, and that block is re-sent on every round
    # of every scenario.
    #
    # Default off because it trades tokens for a reconstruction step: the model
    # has to join a directory header to a filename, and a path it gets wrong is
    # a 404 — which this codebase treats as a step towards wrongly concluding a
    # feature is absent. Measured at 2-4% on a shallow repository, in a run
    # where two scenarios also moved from "pass" to "inconclusive". One run
    # cannot prove the format caused that, but a few percent does not justify
    # the doubt.
    #
    # Worth revisiting on a deep repository that fills _TREE_PATH_CAP, where
    # the same grouping is worth far more. Confirm verdicts are unchanged
    # across at least two runs before leaving it on.
    verification_compact_tree: bool = False

    # Give a scenario the model answered "inconclusive" one more pass, quoting
    # back the evidence it said was missing.
    #
    # On by default because an inconclusive verdict is a declined question, and
    # the model reaches for it early: measured runs returned it after two or
    # three tool calls out of a budget of twenty, and the same scenarios came
    # back inconclusive run after run — a real gap, not noise. Between a quarter
    # and a third of one 16-scenario suite was coming back undecided.
    #
    # It costs a second pass, but only for scenarios that were going to be
    # useless anyway, and the run-scoped read cache makes re-reading free. The
    # retry is written to make the model LOOK, not to make it decide: it is told
    # explicitly that inconclusive remains correct if the code it then reads
    # still does not settle the question. Set false to trade those answers back
    # for the tokens.
    verification_escalate_inconclusive: bool = True

    # One key per vendor, because the frontend model picker means a single
    # deployment talks to more than one of them in the same session.
    #
    # There is deliberately no shared key. `LLM_API_KEY` used to serve
    # whichever provider `LLM_PROVIDER` named, which made the two settings a
    # pair that had to be changed together — and changing one without the other
    # sent a vendor's key to a different vendor, failing as a 401 from an API
    # nobody was thinking about. A variable that names its own vendor cannot be
    # wrong about which vendor it is for.
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Which vendor embeds text for every RAG surface.
    #
    # Deliberately its own setting rather than a consequence of `llm_provider`.
    # An index is built with ONE embedding model: the vectors a query is
    # compared against must come from the same model that produced them, or
    # retrieval silently returns nothing rather than failing. Tying this to the
    # per-request chat picker would mean a ticket ingested while OpenAI was
    # selected is invisible to a question asked while Claude is selected — the
    # write path and the read path would disagree, with nothing on screen to
    # say why. Chat provider stays free to vary per request; this does not.
    #
    # Changing it invalidates the existing index. Point `PINECONE_INDEX_NAME`
    # at a new index of the matching dimension (see `_EMBEDDING_DIMENSIONS`)
    # and re-ingest — a vector of the wrong width is rejected by Pinecone, and
    # one of the right width from the wrong model is worse, because it is not.
    embedding_provider: str = "openai"  # "openai" or "voyage"
    voyage_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"

    # BDD Model Provider (fine-tuned model vs general LLM for BDD generation)
    bdd_model_provider: str = "general_llm"  # "general_llm" or "fine_tuned"
    fine_tuned_model_endpoint: str = ""  # HTTP endpoint for fine-tuned model (Phase 2)
    fine_tuned_model_api_key: str = ""  # API key for fine-tuned model endpoint (Phase 2)
    # Kept well under NFR-P3's 30s BDD-generation budget: when the fine-tuned
    # endpoint hangs, the general-LLM fallback still has to run afterwards, so
    # the HTTP wait and the fallback call together must fit inside 30s.
    # Enforced as a TOTAL wall-clock bound via asyncio.timeout in the provider —
    # httpx's own timeout applies this value to each phase separately and so
    # cannot cap the whole exchange.
    fine_tuned_model_timeout_seconds: float = 12.0
    # Whether a fine-tuned failure may be served by the general LLM instead.
    #
    # Default True preserves the serving decision the provider was built around:
    # an end user should get scenarios rather than an error. Set False when it
    # matters that `fine_tuned` MEANS fine-tuned — a demo, a screenshot, or any
    # measurement — because a silent fallback returns general-LLM output under
    # the fine_tuned label and nothing downstream can tell the difference.
    #
    # Callers that measure this model (the evaluation runner) pass
    # allow_fallback=False explicitly and are unaffected by this setting either
    # way; it only sets the default the serving path uses.
    fine_tuned_allow_fallback: bool = True

    # Whether captured BDD content may be used to fine-tune a model. Stamped
    # onto each bdd_files row AT WRITE TIME, never evaluated when a dataset is
    # built — consent belongs to the moment of capture, so flipping this must
    # not reclassify data that already exists. Rows are always persisted either
    # way; only their training eligibility changes.
    training_data_opt_in: bool = True

    # Symmetric key for credentials stored per project (Jira / Confluence /
    # GitHub tokens). NOT a hash salt: those credentials are replayed to their
    # APIs, so they must be recoverable. Generate with:
    #   python -c "from app.core.crypto import generate_key; print(generate_key())"
    #
    # Kept out of the database on purpose. Rotating it makes every stored
    # credential unreadable, which surfaces as a clear error rather than as a
    # silent authentication failure against someone else's API.
    credential_encryption_key: str = ""

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

    # Minimum similarity score (cosine, 0..1) a knowledge-base chunk must reach to
    # be treated as relevant. Retrieval returns top-K unconditionally, so without
    # this floor a scenario shows loosely-related/irrelevant context. Raise toward
    # ~0.4 for stricter relevance, lower toward ~0.2 to keep more context.
    rag_min_score: float = 0.3

    # Fine-tuning runs (training/kaggle_run.py, driven from the fine-tune page)
    #
    # Where the training/ scripts live. Empty means "derive from this package's
    # own location", which is right for a checkout; a deployment that mounts the
    # repo elsewhere sets it. The backend never imports those scripts — they run
    # as subprocesses — so this is a path, not an import root.
    training_repo_root: str = ""
    # How often to ask Kaggle whether the GPU kernel has finished. Each poll is
    # a process launch that re-authenticates, so this is deliberately not tight:
    # the run itself takes tens of minutes and nothing is gained by asking more
    # often than a person would refresh.
    training_poll_seconds: int = 60
    # Give up waiting after this long. The kernel may still finish on Kaggle
    # -- the run says so rather than claiming the training failed, and
    # `kaggle_run.py --fetch` collects it afterwards.
    #
    # Six hours, not two. Two was set when a run trained on ~200 pairs and
    # finished inside an hour. A 2,836-pair run measured 129 minutes of
    # training plus 147 of holdout scoring, so it tripped a 7200s timeout
    # while the kernel was still healthy -- and the row then read `failed`
    # for a run that went on to succeed. Waiting longer costs nothing: the
    # poll is one process launch a minute.
    training_timeout_seconds: int = 21600

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
        # The repo-root .env is shared with tooling the backend knows nothing
        # about — KAGGLE_USERNAME / KAGGLE_KEY for training/kaggle_run.py, which
        # training/KAGGLE.md instructs you to put there. pydantic-settings
        # defaults to forbidding unknown keys, so without this the documented
        # training setup makes every backend test fail at import with
        # `extra_forbidden`, pointing at Kaggle rather than at this model.
        "extra": "ignore",
    }


settings = Settings()
