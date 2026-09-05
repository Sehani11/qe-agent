# BDD-AutoGen Backend

FastAPI service for **qe-agent-v2** — Jira → BDD generation, GitHub code verification,
user auth, and a project knowledge base with RAG-enriched verification.

## Quick start

```bash
cd backend
pip install uv
uv sync --extra dev                        # one-time: install deps
cp ../.env.example .env                     # then fill in the values (see below)
uv run alembic upgrade head                 # apply DB migrations
uv run uvicorn app.main:app --reload --port 8000   # docs at /docs
```

Run the tests:

```bash
uv run pytest
```

## Environment variables

All settings are declared and validated at startup in
[`app/core/config.py`](app/core/config.py). The canonical, fully-commented list
lives in [`../.env.example`](../.env.example) — copy it to `backend/.env` and fill
it in. The app loads `.env` from the directory it is launched in, so keep your
real values in `backend/.env` when running from `backend/`.

Grouped by feature area:

| Group | Keys | Needed for |
|---|---|---|
| **Database** | `DATABASE_URL`, `DIRECT_DATABASE_URL` | Everything (Supabase Postgres; direct URL is Alembic-only) |
| **LLM** | `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | BDD generation + code verification + RAG Q&A |
| **Jira** | `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_USER_EMAIL` | Ticket ingestion (Epic 1) + Jira workspace knowledge (Epic 4). `JIRA_BASE_URL` also builds the Jira links in the RAG context panel (Story 4.4). |
| **GitHub** | `GITHUB_ACCESS_TOKEN` | Code fetching for verification (Epic 2) |
| **Supabase** | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`, `SUPABASE_BUCKET` | Auth, session persistence, storage (Epic 3) |
| **Pinecone** | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | Vector store — BDD vectorisation (Epic 1) **and** the RAG knowledge base (Epic 4) |
| **Confluence** | `CONFLUENCE_BASE_URL`, `CONFLUENCE_API_TOKEN`, `CONFLUENCE_USER_EMAIL` | Confluence knowledge ingestion (Epic 4, Story 4.1) |

### Epic 4 (Knowledge Base & RAG) requirements

Epic 4 adds project-knowledge ingestion and RAG-enriched verification. To exercise
it end-to-end you need:

- **`PINECONE_API_KEY`** (+ a matching `PINECONE_INDEX_NAME`) — the vector store all
  ingestion and retrieval depend on.
- **`CONFLUENCE_*`** — for Confluence page ingestion. Jira workspace ingestion reuses
  the existing `JIRA_*` keys.

**Graceful degradation:** if `PINECONE_API_KEY` is unset, the app still runs — knowledge
ingestion becomes a no-op and verification proceeds without project context. In that
mode the RAG context panel (Story 4.4) shows *"No additional project context available"*
rather than erroring.
