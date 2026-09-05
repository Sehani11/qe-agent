# Story 4.1: Knowledge Base Service & Confluence Integration

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): Confluence credentials come from the active project when it has them, resolved all-or-nothing (a project base URL is never paired with an environment token). Pages are embedded into the project's namespace, and deep links are built from the base URL the page was actually fetched from rather than the environment's.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ingest-by-url.md)): `ConfluenceIngestRequest` gained `page_refs` (page URLs and/or bare numeric ids), checked before `space_key` and `page_id`. New `confluence_service.extract_page_id()` parses the id out of a page URL; short `/wiki/x/` links are rejected with a message naming the input, since they carry no id. Refs are deduplicated by extracted id before fetching.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to connect my Confluence workspace and ingest project documentation into the knowledge base,
So that the system has access to my project's architectural decisions, guidelines, and related documentation.

## Acceptance Criteria

1. **Given** an authenticated user provides Confluence API credentials (base URL + API token)
   **When** a POST request is made to `/api/v1/knowledge/ingest/confluence` with workspace and page/space identifiers
   **Then** the FastAPI `knowledge_service.py` fetches Confluence pages via `confluence_service.py`, chunks the content, and embeds it into Pinecone under namespace `{user_id}:knowledge` (FR39, FR40, NFR-S8)

2. **And** a `knowledge_sources` table row is created tracking the ingested source type, URL, and ingestion timestamp

3. **And** the ingestion streams SSE progress events showing pages being processed

4. **And** if Confluence API fetch fails, a clear error is emitted via SSE with actionable messaging (NFR-R1)

5. **And** a Pytest test verifies that knowledge embeddings are namespaced per user and not accessible cross-user

## Tasks / Subtasks

### Backend Tasks

- [x] **Task 1: Create `KnowledgeSource` ORM model** (AC: 2)
  - [x] Create `backend/app/models/knowledge_source.py` with SQLAlchemy model
  - [x] Fields: `id` (UUID PK), `user_id` (String, indexed), `source_type` (String: "confluence"/"jira"), `source_url` (String), `title` (String), `page_count` (Integer, default 0), `ingestion_status` (String: "pending"/"completed"/"failed"), `created_at` (DateTime UTC)
  - [x] Follow exact pattern from `backend/app/models/session.py` (UUID PK, `datetime.now(UTC)`)
  - [x] Update `backend/app/models/__init__.py` to import and export `KnowledgeSource`

- [x] **Task 2: Create `knowledge.py` Pydantic schemas** (AC: 1, 2, 3)
  - [x] Create `backend/app/schemas/knowledge.py`
  - [x] `ConfluenceIngestRequest`: `space_key: str | None = None`, `page_id: str | None = None` — at least one required (validate in service)
  - [x] `KnowledgeSourceResponse`: `id`, `user_id`, `source_type`, `source_url`, `title`, `page_count`, `ingestion_status`, `created_at`
  - [x] All field names `snake_case` to match backend JSON output convention

- [x] **Task 3: Create `confluence_service.py`** (AC: 1, 4)
  - [x] Create `backend/app/services/confluence_service.py`
  - [x] `ConfluenceServiceError(Exception)` with `message` and `code` attrs — same pattern as `JiraServiceError`
  - [x] `ConfluencePage` Pydantic model: `id`, `title`, `url`, `body` (plain text stripped from HTML)
  - [x] `async def fetch_pages_from_space(space_key: str) -> list[ConfluencePage]`
    - Uses Confluence REST API v1: `GET {confluence_base_url}/wiki/rest/api/content?spaceKey={space_key}&type=page&expand=body.storage&limit=50`
    - Auth: `httpx.BasicAuth(settings.confluence_user_email, settings.confluence_api_token)`
    - Strip HTML from `body.storage.value` — use `re.sub(r'<[^>]+>', '', html_body)` then `html.unescape()`
    - Handle pagination via `_links.next` in the response
    - Raise `ConfluenceServiceError` with code `CONFLUENCE_FETCH_FAILED` on any HTTP error
  - [x] `async def fetch_page_by_id(page_id: str) -> ConfluencePage`
    - Uses: `GET {confluence_base_url}/wiki/rest/api/content/{page_id}?expand=body.storage`
    - Same auth, HTML stripping, and error handling
  - [x] Guard: if `settings.confluence_base_url` or `settings.confluence_api_token` missing → raise `ConfluenceServiceError("Confluence credentials not configured.", code="CONFLUENCE_NOT_CONFIGURED")`

- [x] **Task 4: Create `knowledge_service.py`** (AC: 1, 2, 3, 4)
  - [x] Create `backend/app/services/knowledge_service.py`
  - [x] `async def ingest_confluence(user_id, request, db) -> AsyncGenerator[str, None]:`
    - This is the SSE generator — follow exact pattern from `verification_service.py`
    - Step 1: yield `data: {"type": "progress", "message": "Connecting to Confluence..."}\n\n`
    - Step 2: call `confluence_service.fetch_pages_from_space(space_key)` OR `fetch_page_by_id(page_id)`
    - Step 3: for each page, yield `data: {"type": "progress", "message": "Processing: {page.title}", "current": i, "total": N}\n\n`
    - Step 4: call `chunk_text(page.body)` imported from `app.services.vector_service`
    - Step 5: embed chunks and upsert to Pinecone namespace `{user_id}:knowledge` — use existing embedding logic from `vector_service.py`
    - Step 6: create `KnowledgeSource` DB row per page (source_type="confluence", source_url=page.url, title=page.title, page_count=len(pages), ingestion_status="completed")
    - Step 7: yield `data: {"type": "complete", "ingested_count": N}\n\n`
    - On `ConfluenceServiceError`: yield `data: {"type": "error", "error": exc.code, "message": exc.message}\n\n` then return
    - On any other exception: yield `data: {"type": "error", "error": "KNOWLEDGE_INGEST_FAILED", "message": "Ingestion failed. Please try again."}\n\n`
  - [x] **CRITICAL — Pinecone vector IDs**: use `f"confluence_{page.id}_chunk_{i}"` to allow safe re-ingestion without duplicates (upsert semantics)
  - [x] **CRITICAL — Namespace isolation**: ALWAYS use `f"{user_id}:knowledge"` — never `settings.dev_user_id`

- [x] **Task 5: Create `knowledge.py` route handler** (AC: 1, 3, 4)
  - [x] Create `backend/app/api/v1/knowledge.py`
  - [x] `router = APIRouter()`
  - [x] `@router.post("/ingest/confluence")` returns `StreamingResponse`
  - [x] Inject: `request: ConfluenceIngestRequest`, `db: AsyncSession = Depends(get_db)`, `current_user: str = Depends(get_current_user)`
  - [x] Validate: `if not request.space_key and not request.page_id` → return `JSONResponse(status_code=422, content={"error": "INVALID_REQUEST", "message": "Provide space_key or page_id.", "code": 422})`
  - [x] Call `knowledge_service.ingest_confluence(user_id=current_user, request=request, db=db)`
  - [x] Return `StreamingResponse(stream, media_type="text/event-stream")`
  - [x] No business logic in this handler — only delegation to service

- [x] **Task 6: Register knowledge router in API** (AC: 1)
  - [x] Edit `backend/app/api/v1/api.py`: import `from app.api.v1 import knowledge`
  - [x] Add: `api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])`

- [x] **Task 7: Create Alembic migration for `knowledge_sources` table** (AC: 2)
  - [x] Run `alembic revision --autogenerate -m "add knowledge_sources table"` inside the backend container
  - [x] Review the generated migration and verify all columns: `id UUID PK`, `user_id VARCHAR NOT NULL`, `source_type VARCHAR NOT NULL`, `source_url VARCHAR`, `title VARCHAR`, `page_count INTEGER DEFAULT 0`, `ingestion_status VARCHAR NOT NULL DEFAULT 'pending'`, `created_at TIMESTAMPTZ`
  - [x] Add index on `user_id` column
  - [x] Apply: `alembic upgrade head`

- [x] **Task 8: Write Pytest tests** (AC: 5)
  - [x] Create `backend/tests/test_knowledge.py`
  - [x] Test: `POST /api/v1/knowledge/ingest/confluence` returns `StreamingResponse` (200) for valid request
  - [x] Test: `POST /api/v1/knowledge/ingest/confluence` returns 422 when neither `space_key` nor `page_id` is provided
  - [x] Test: `POST /api/v1/knowledge/ingest/confluence` returns 401 without auth token
  - [x] Test: namespace isolation — mock Pinecone upsert and assert namespace is `f"{user_id}:knowledge"` (NOT dev_user_id, NOT another user's ID)
  - [x] Test: when `ConfluenceServiceError` is raised, SSE stream yields `{"type": "error", ...}` event
  - [x] Test: on success, `knowledge_sources` row is created in DB with `source_type="confluence"` and `ingestion_status="completed"`
  - [x] Follow mocking patterns from existing `test_sessions.py` — use `AsyncMock`, `MagicMock`, `app.dependency_overrides` for `get_current_user` and `get_db`

### Frontend Tasks

- [x] **Task 9: Create TypeScript types for knowledge base** (AC: 1)
  - [x] Create `frontend/src/lib/types/knowledge.ts`
  - [x] `ConfluenceIngestRequest`: `{ spaceKey?: string; pageId?: string }`  — camelCase (transformed by axios interceptor from snake_case)
  - [x] `KnowledgeSource`: `{ id: string; userId: string; sourceType: string; sourceUrl: string; title: string; pageCount: number; ingestionStatus: string; createdAt: string }`
  - [x] `KnowledgeSSEEvent`: `{ type: 'progress' | 'complete' | 'error'; message?: string; current?: number; total?: number; ingestedCount?: number; error?: string }`

- [x] **Task 10: Create `useKnowledge.ts` TanStack Query hook** (AC: 1, 3)
  - [x] Create `frontend/src/lib/hooks/useKnowledge.ts`
  - [x] `useIngestConfluence()` — TanStack Query `useMutation` that POSTs to `/api/v1/knowledge/ingest/confluence`
    - The SSE stream response should be consumed via `fetch` with `ReadableStream` (or reuse `useSSEStream.ts` if applicable)
    - On success events: update local state with progress messages
    - Mutation input: `ConfluenceIngestRequest`
  - [x] No raw fetch/axios calls in components — all API interaction through this hook
  - [x] No `any` type — use `KnowledgeSSEEvent` for SSE event parsing
  - [x] Export: `export { useIngestConfluence }`

## Dev Notes

### Critical: Pinecone Namespace Isolation for Knowledge Base

The knowledge base uses a **different** namespace from ticket Q&A:

| Use case | Pinecone namespace | Where used |
|---|---|---|
| Ticket Q&A (Epic 1) | `{user_id}:{session_id}` | `vector_service.embed_and_index_ticket()` |
| Project knowledge base | `{user_id}:knowledge` | `knowledge_service.py` (this story) |

**Never** cross-contaminate these namespaces. The `user_id` always comes from `get_current_user()` JWT dependency — never from `settings.dev_user_id` in Epic 4+.

### Critical: Reuse `chunk_text()` from `vector_service.py`

Do NOT duplicate chunking logic. Import directly:

```python
from app.services.vector_service import chunk_text
```

The same semantic chunking (500-word chunks, 50-word overlap) applies to Confluence content.

### Critical: Confluence HTML Body Stripping

Confluence REST API v1 returns page content as HTML in `body.storage.value`. You must strip all HTML tags before chunking:

```python
import html
import re

def strip_html(raw: str) -> str:
    text = re.sub(r'<[^>]+>', '', raw)
    return html.unescape(text).strip()
```

Do not use BeautifulSoup — it's not in the project dependencies. The regex approach is sufficient for Confluence's structured HTML.

### SSE Streaming Pattern (match verification_service.py)

The existing SSE pattern in `verification_service.py` must be followed exactly:

```python
# Route handler (knowledge.py):
stream = knowledge_service.ingest_confluence(...)
return StreamingResponse(stream, media_type="text/event-stream")

# Service generator (knowledge_service.py):
async def ingest_confluence(...) -> AsyncGenerator[str, None]:
    yield f"data: {json.dumps({'type': 'progress', 'message': 'Connecting...'})}\n\n"
    # ... work ...
    yield f"data: {json.dumps({'type': 'complete', 'ingested_count': N})}\n\n"
```

### Confluence API Authentication

Use `httpx.BasicAuth` — same pattern as `jira_service.py`:

```python
auth = httpx.BasicAuth(settings.confluence_user_email, settings.confluence_api_token)
async with httpx.AsyncClient(auth=auth, timeout=30.0) as client:
    response = await client.get(url)
    response.raise_for_status()
```

### Config: Confluence Credentials Already Exist

Do **not** add new env vars to `config.py` — they are already present:

```python
# backend/app/core/config.py (lines ~54-57)
confluence_base_url: str = ""
confluence_api_token: str = ""
confluence_user_email: str = ""
```

### Alembic: models/__init__.py Must Be Updated First

Alembic `--autogenerate` discovers models via `metadata` on `Base`. The `KnowledgeSource` model must be imported in `backend/app/models/__init__.py` **before** running the migration command, otherwise it will not appear in the generated migration.

### Architecture Compliance Checklist

- [x] Route handler (`knowledge.py`) contains NO business logic — only validation + delegation to service
- [x] All Confluence HTTP calls are in `confluence_service.py` — never in `knowledge_service.py` directly
- [x] Pinecone upsert in `knowledge_service.py` uses namespace `f"{user_id}:knowledge"` — never `settings.dev_user_id`
- [x] Auth guard: `get_current_user` dependency applied on the ingest endpoint
- [x] Error responses match envelope: `{"error": "CODE", "message": "Human-readable.", "code": N}`
- [x] No TypeScript `any` types — use `KnowledgeSSEEvent` union for SSE event parsing
- [x] Frontend API calls go through hook only — no raw axios in components

### Project Structure Notes

- Alignment with unified project structure:
  - Backend model: `backend/app/models/knowledge_source.py` ← defined in architecture
  - Backend schema: `backend/app/schemas/knowledge.py` ← defined in architecture
  - Backend service: `backend/app/services/knowledge_service.py` + `confluence_service.py` ← defined in architecture
  - Backend route: `backend/app/api/v1/knowledge.py` ← defined in architecture
  - Backend tests: `backend/tests/test_knowledge.py` ← defined in architecture
  - Frontend types: `frontend/src/lib/types/knowledge.ts` ← defined in architecture
  - Frontend hook: `frontend/src/lib/hooks/useKnowledge.ts` ← defined in architecture (`useKnowledge.ts`)

- No conflicts or variances detected; all paths match architecture.md project structure exactly.

### References

- Story 4.1 AC: [epics.md](/_bmad-output/planning-artifacts/epics.md#story-41-knowledge-base-service--confluence-integration)
- Architecture — Knowledge Base section: [architecture.md](/_bmad-output/planning-artifacts/architecture.md#project-knowledge-base)
- Architecture — Project Structure: [architecture.md](/_bmad-output/planning-artifacts/architecture.md#complete-project-directory-structure)
- Mandatory Architecture Rules: [architecture.md](/_bmad-output/planning-artifacts/architecture.md#mandatory-rules--all-ai-agents-must-follow) (rules 1–10)
- Pinecone namespace conventions: [architecture.md](/_bmad-output/planning-artifacts/architecture.md#data-architecture) — `{user_id}:knowledge`
- Existing chunking utility: [services/vector_service.py](backend/app/services/vector_service.py#L22) — `chunk_text()`
- SSE streaming pattern: [api/v1/verification.py](backend/app/api/v1/verification.py) + [services/verification_service.py](backend/app/services/verification_service.py)
- Model pattern reference: [models/session.py](backend/app/models/session.py)
- Service error pattern: [services/jira_service.py](backend/app/services/jira_service.py#L22) — `JiraServiceError` pattern
- Config (Confluence vars): [core/config.py](backend/app/core/config.py#L54)
- Models __init__: [models/__init__.py](backend/app/models/__init__.py) — must add `KnowledgeSource`
- API router: [api/v1/api.py](backend/app/api/v1/api.py) — must include knowledge router
- Auth dependency: [core/auth.py](backend/app/core/auth.py) — `get_current_user`
- TanStack Query hook pattern: [hooks/useSession.ts](frontend/src/lib/hooks/useSession.ts)
- Axios client (snake→camelCase interceptor): [lib/api/](frontend/src/lib/api/)
- FR39: Knowledge source ingestion — FR40: Namespace isolation — NFR-S8: No cross-user retrieval
- Previous epic's last story (learnings): [3-5-session-history-dashboard.md](_bmad-output/implementation-artifacts/3-5-session-history-dashboard.md)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

- Ruff E501 (line too long) fixed in all new files by extracting `_sse()` helper in `knowledge_service.py` and wrapping long strings
- Ruff RUF100 (unused noqa) fixed: removed `# noqa: B008` from `get_current_user` dependency (only `get_db` needs it)
- Ruff SIM108 fixed: replaced if/else pagination block with ternary in `confluence_service.py`
- Pre-existing TypeScript errors in `src/lib/supabase/client.ts` and `src/proxy.ts` (missing `@supabase/ssr` package) — unrelated to Story 4.1, no errors in any knowledge-related files

### Completion Notes List

- Created `backend/app/models/knowledge_source.py`: `KnowledgeSource` ORM model with `id` (UUID PK), `user_id` (indexed), `source_type`, `source_url`, `title`, `page_count`, `ingestion_status`, `created_at`
- Updated `backend/app/models/__init__.py`: added `KnowledgeSource` import and export
- Created `backend/app/schemas/knowledge.py`: `ConfluenceIngestRequest` (space_key | page_id) and `KnowledgeSourceResponse`
- Created `backend/app/services/confluence_service.py`: `ConfluenceServiceError`, `ConfluencePage`, `_strip_html()`, `_check_credentials()`, `fetch_pages_from_space()` with pagination, `fetch_page_by_id()`
- Created `backend/app/services/knowledge_service.py`: `ingest_confluence()` SSE async generator with `_sse()` helper, `_embed_chunks()`, `_upsert_to_pinecone()`; namespace always `{user_id}:knowledge`; reuses `chunk_text()` from `vector_service.py`
- Created `backend/app/api/v1/knowledge.py`: `POST /ingest/confluence` returning `StreamingResponse`; validation guard for missing space_key/page_id; no business logic
- Updated `backend/app/api/v1/api.py`: registered knowledge router at prefix `/knowledge`
- Created `backend/alembic/versions/e3f4a5b6c7d8_add_knowledge_sources_table.py`: migration adds `knowledge_sources` table with `ix_knowledge_sources_user_id` index; down_revision `d2e3f4a5b6c7`
- Created `backend/tests/test_knowledge.py`: 15 tests — route tests (200/422/401), service generator tests (namespace isolation, ConfluenceServiceError handling, DB row creation, cross-user isolation), confluence service unit tests (HTML stripping, credentials guard); all 15 pass
- 162 total backend tests passing (0 regressions); ruff linting clean on all new files
- Created `frontend/src/lib/types/knowledge.ts`: `ConfluenceIngestRequest`, `KnowledgeSource`, `KnowledgeSSEEvent` types
- Created `frontend/src/lib/hooks/useKnowledge.ts`: `useIngestConfluence()` hook consuming SSE via `fetch`/`ReadableStream`, updating progress state, no `any` types

### File List

backend/app/models/knowledge_source.py (new)
backend/app/models/__init__.py (modified — added KnowledgeSource)
backend/app/schemas/knowledge.py (new)
backend/app/services/confluence_service.py (new)
backend/app/services/knowledge_service.py (new)
backend/app/api/v1/knowledge.py (new)
backend/app/api/v1/api.py (modified — added knowledge router)
backend/alembic/versions/e3f4a5b6c7d8_add_knowledge_sources_table.py (new)
backend/tests/test_knowledge.py (new)
frontend/src/lib/types/knowledge.ts (new)
frontend/src/lib/hooks/useKnowledge.ts (new)

## Change Log

- 2026-05-02: Implemented Story 4.1 — Knowledge Base Service & Confluence Integration. Created KnowledgeSource model, Alembic migration, Confluence service with HTML stripping and pagination, knowledge ingestion service with SSE streaming and Pinecone namespace isolation, route handler, knowledge router registration, 15 Pytest tests (all passing, 162 total no regressions), TypeScript types and useKnowledge hook.
