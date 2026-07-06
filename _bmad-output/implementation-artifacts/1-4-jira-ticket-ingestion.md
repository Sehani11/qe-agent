# Story 1.4: jira-ticket-ingestion

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to submit a Jira ticket URL or ID and have the ticket content fetched, chunked, and embedded into Pinecone,
So that the ticket is indexed and ready for BDD generation and future RAG Q&A.

## Acceptance Criteria

1. [x] **Given** a POST request to `/api/v1/ingestion/ingest` with a valid Jira ticket URL or ID, **When** the FastAPI Jira service fetches the ticket content (Summary, Description, Acceptance Criteria, Labels, Linked Issues) using the user's Jira credentials, **Then** the ticket content is chunked and embedded into Pinecone under namespace `DEV_USER_ID:{session_id}` (NFR-S4).
2. [x] **And** the endpoint returns a standard JSON response containing `session_id`, `jira_ticket_id`, `acceptance_criteria`, and a `ready_for_bdd` status on success (NFR-P5).
3. [x] **And** a `sessions` table row is created in the database with `id` (as `session_id`), and `jira_ticket_id`.
4. [x] **And** if Jira fetch fails (invalid ticket, auth failure, API error) the endpoint returns a structured JSON error with a human-readable message — no raw error codes (FR8, NFR-R1, NFR-S7).
5. [x] **And** ingestion completes within 30 seconds for a typical ticket up to 5,000 words (NFR-P1).

## Tasks / Subtasks

- [x] Task 1: Define Schemas & Database Models
  - [x] Create SQLAlchemy `Session` model in `backend/app/models/session.py` with `id` (UUID), `user_id` (String), `jira_ticket_id` (String), and `created_at` (DateTime).
  - [x] Generate an Alembic migration for this tables and apply it to the local DB.
  - [x] Define the Pydantic request schema for `/ingest` receiving `ticket_id_or_url`.
- [x] Task 2: Implement Jira Fetch Service
  - [x] Create `backend/app/services/jira_service.py`.
  - [x] Implement `fetch_ticket_content` to call Jira REST API v3 using `JIRA_API_URL` and `JIRA_API_TOKEN` env vars using `httpx.AsyncClient` with a strict `timeout=30.0` (NFR-P1).
  - [x] Extract Summary, Description, Acceptance Criteria, Labels, and Linked Issues into a structured format.
  - [x] Handle validation and specific Jira HTTP errors (e.g., 401, 404).
- [x] Task 3: Implement Pinecone Embedding & Indexing
  - [x] Create `backend/app/services/vector_service.py` to handle chunking and embeddings.
  - [x] Utilize the existing configured LLM provider (or raw OpenAI embeddings endpoint if needed) to generate vector embeddings for chunked Jira content.
  - [x] Upsert vectors into Pinecone using the strict namespace `{settings.dev_user_id}:{session_id}`.
- [x] Task 4: Expose Jira Ingestion API Endpoint
  - [x] Create `backend/app/api/v1/ingestion.py`.
  - [x] Implement `POST /ingest` as a standard FastAPI JSON endpoint returning the session payload required for the BDD step.
  - [x] Return `session_id`, `jira_ticket_id`, and `acceptance_criteria` in the success payload so the frontend can transition directly into BDD generation.
  - [x] Map Jira and vector-service failures to structured HTTP errors with safe, human-readable messages.
  - [x] Create the database `Session` row using an async SQLAlchemy session upon successful finalization.
  - [x] Register router in `backend/app/api/v1/api.py`.
- [x] Task 5: Testing
  - [x] Create `backend/tests/test_ingestion.py`.
  - [x] Mock the Jira API `httpx.AsyncClient` responses and Pinecone interactions.
  - [x] Verify JSON success response structure and database insertion.
  - [x] Verify structured error responses for Jira fetch failures mapping properly to `JIRA_FETCH_FAILED` semantics.

## Dev Notes

### Dev Agent Guardrails

- **Tooling:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Pinecone, `httpx`.
- **Response Contract:** Ingestion now uses a plain JSON response contract. Return only the data the frontend needs to continue the flow: `session_id`, `jira_ticket_id`, `acceptance_criteria`, and status.
- **Error Obfuscation:** Do NOT expose raw Jira API exceptions. Catch them, log securely inside the server if needed, and map them to a safe structured HTTP error response.
- **Database Context:** DB operations must be async (using `asyncpg` and SQLAlchemy async sessions).
- **Timeouts:** Ensure `httpx.AsyncClient` instances pass explicit timeouts to satisfy NFR-P1.

### Previous Story Intelligence

From Story 1.3 (`bdd-generation-api`):
- **LLM Timouts:** Learned that third party API wrappers must enforce strict timeouts (NFR-P3 -> 30.0s). Ensure `httpx` logic explicitly adds this.
- **Robustness:** Built resilient JSON parsing with regex. Make sure chunking logic cleanly handles markup content if present in Jira tickets.
- **Route Patterns:** Continue enforcing that `app/api/...` only handles routing and response formats, pushing all business logic down to `services/`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.4]
- [Source: _bmad-output/planning-artifacts/architecture.md#API Patterns]

## Dev Agent Record

### Agent Model Used

Gemini 2.5 Pro

### Debug Log References

- Set `aiosqlite` and `sqlite+aiosqlite` configs for testing when `postgresql` backend docker wasn't running/blocked.
- Addressed `RuntimeWarning` from `AsyncMock` on non-awaited DB functions inside pytest.
- Generated `create_sessions_table` alembic migration successfully.

### Completion Notes List

- ✅ **Task 1:** Defined `Session` data model bridging `user_id`, `jira_ticket_id` constraints in `session.py`. Wrote `IngestRequest` structure inside API schemas. Generated `b37b53deea53_create_sessions_table` via Alembic to sync the schema.
- ✅ **Task 2:** Completed `fetch_ticket_content` utilizing Atlassian V3 robust queries in `jira_service.py` using `httpx.AsyncClient` + NFR-P1 compliant `timeout=30.0` args mapping specific missing custom fields.
- ✅ **Real Jira Hardening:** Extended Jira fetch to request `expand=names`, detect real custom field display names, extract acceptance criteria from named custom fields or description sections, and fall back to usable description text for BDD generation when a dedicated AC field is absent.
- ✅ **Task 3:** Implemented raw chunking algorithms and `pinecone` integration upserting vectors wrapped to the exact namespace `{user_id}:{session_id}` in `vector_service.py`.
- ✅ **Task 4:** Finalized `POST /api/v1/ingestion/ingest` as a standard JSON endpoint returning the session payload needed for the next BDD step. Mounted safely in `api.py`.
- ✅ **UI Integration Follow-up:** The success response now includes `acceptance_criteria` directly so the frontend can transition from ticket ingest into BDD generation without refetching Jira content.
- ✅ **Runtime Simplification:** Removed the previous SSE-based ingest transport in favor of a simpler request/response flow while preserving the same user-facing loader-based progress experience.
- ✅ **Task 5:** Updated API tests to validate JSON success and structured error responses in `test_ingestion.py`.
- ✅ **Review Fix (High/Medium):** Replaced default randomized embeddings mock inside Pinecone indexing to `httpx.post` queries pointing straight into OpenAI standard embeddings endpoint conditionally wrapped securely by `asyncio.to_thread` event loops natively, masked external error leaks completely, documented undocumented modified endpoints, and injected explicit semantic regex chunking mechanisms in lieu of string space counts.

### File List

- `backend/app/models/base.py` (created)
- `backend/app/models/session.py` (created)
- `backend/app/models/__init__.py` (modified)
- `backend/app/schemas/ingestion.py` (created)
- `backend/alembic/env.py` (modified)
- `backend/alembic/versions/b37b53deea53_create_sessions_table.py` (created)
- `backend/app/services/jira_service.py` (created)
- `backend/app/services/vector_service.py` (created)
- `backend/app/api/v1/ingestion.py` (created)
- `backend/app/api/v1/api.py` (modified)
- `backend/app/api/v1/bdd.py` (modified)
- `backend/app/services/llm/claude_provider.py` (modified)
- `backend/tests/test_ingestion.py` (created)
- `backend/tests/test_jira_service.py` (created)
- `backend/.env` (modified)
