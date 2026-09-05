# Story 3.3: Session Persistence & Database Schema

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): `sessions.project_id` was added (NOT NULL) and every existing session was migrated into a per-user `Project-1`. Session creation resolves a project via `ensure_project()`; there is no path that writes a session without one.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): The `sessions` table gained `jira_ticket_url` (nullable, migration `f6b7c8d9e0a1`) — the reference the user submitted at ingestion, used to restore the ticket field on revisit.

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-source-persistence.md)): `verification_results` gained `verification_mode` (String(20)) and `github_input` (Text), both nullable, migration `a1b2c3d4e5f6` (NOT yet applied). Stored per row rather than per session because results accumulate — one session can hold several runs against different sources.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want all my sessions, BDD content, and verification results automatically saved,
So that my work is never lost and I can return to it at any time.

## Acceptance Criteria

1. **Given** a user completes Jira ingestion
   **When** the session is created in the backend
   **Then** a row is inserted into the `sessions` table with `id`, `user_id`, `jira_ticket_id`, `created_at` (FR31) — **already implemented in Epic 1 ingestion route; no change needed**

2. **And** when BDD content is generated via `POST /api/v1/bdd/generate`
   **Then** the generated Gherkin text is persisted to the `bdd_files` table with `session_id`, `user_id`, `content`, `source` = `"generated"`, and `created_at`

3. **And** when a `.feature` file is uploaded via `POST /api/v1/bdd/upload`
   **Then** the uploaded file's content is persisted to the `bdd_files` table with `source` = `"uploaded"`

4. **And** Supabase RLS policies are applied to `sessions`, `bdd_files`, `chat_messages`, and `verification_results` tables so all operations are scoped to `auth.uid() = user_id` (FR34, NFR-S6)

5. **And** Alembic migration files exist for all four tables (`sessions` ✅, `verification_results` ✅, `bdd_files` ← new, `chat_messages` ← new)

6. **And** a user cannot read another user's session data even if the `session_id` is known — enforced by FastAPI `user_id` checks in all routes AND Supabase RLS

7. **And** a `GET /api/v1/sessions` route returns the current user's sessions (needed as foundation for Story 3.5)

8. **And** a `GET /api/v1/sessions/{session_id}` route returns a single session's details, returning 404 if not found and 403 if the session belongs to another user

## Tasks / Subtasks

- [x] **Task 1: Create `BddFile` SQLAlchemy model** (AC: 2, 3, 5)
  - [x] Create `backend/app/models/bdd_file.py` with fields: `id` (UUID PK), `session_id` (String FK-like, indexed), `user_id` (String, indexed), `content` (Text), `source` (String(20): `"generated"` or `"uploaded"`), `created_at` (DateTime timezone)
  - [x] Register in `backend/app/models/__init__.py` alongside existing models
  - [x] Create Alembic migration: `c1f2a3b4d5e6_create_bdd_files_table.py` — manually authored, schema matches model exactly
  - [x] Migration must chain: `down_revision = "a9d4e72f1c83"` (the verification_results migration)

- [x] **Task 2: Create `ChatMessage` SQLAlchemy model** (AC: 4, 5)
  - [x] Create `backend/app/models/chat_message.py` with fields: `id` (UUID PK), `session_id` (String, indexed), `user_id` (String, indexed), `role` (String(20): `"user"` or `"assistant"`), `content` (Text), `created_at` (DateTime timezone)
  - [x] Register in `backend/app/models/__init__.py`
  - [x] Create Alembic migration: `d2e3f4a5b6c7_create_chat_messages_table.py` — chains off bdd_files migration

- [x] **Task 3: Persist BDD content on generate** (AC: 2)
  - [x] Update `backend/app/api/v1/bdd.py` `generate_bdd` route to:
    - Add `db: AsyncSession = Depends(get_db)` and `current_user: str = Depends(get_current_user)` (already has `current_user`)
    - After `generate_bdd_scenarios()` succeeds, create and save a `BddFile` row: `session_id=str(request.session_id)`, `user_id=current_user`, `content=<gherkin_text>`, `source="generated"`
    - `db.add(bdd_file); await db.commit()`
  - [x] Content stored as `json.dumps(response.model_dump())` — preserves scenario metadata and AC traceability for Story 3.5
  - [x] Import `AsyncSession`, `get_db`, `BddFile` in `bdd.py`

- [x] **Task 4: Persist BDD content on upload** (AC: 3)
  - [x] Investigated — no backend upload route existed (Story 1.6 was client-side Supabase Storage only)
  - [x] Created `POST /api/v1/bdd/upload` accepting multipart `.feature` file + `session_id`, saves to `bdd_files` with `source="uploaded"`, returns content
  - [x] Added `BDDUploadResponse` schema to `backend/app/schemas/bdd.py`

- [x] **Task 5: Create Sessions API routes** (AC: 7, 8)
  - [x] Created `backend/app/api/v1/sessions.py` with two routes:
    - `GET /api/v1/sessions` → query `sessions` table WHERE `user_id == current_user`, order by `created_at DESC`, return list
    - `GET /api/v1/sessions/{session_id}` → query single session; if not found → 404; if `session.user_id != current_user` → 403
  - [x] Created Pydantic schema `SessionResponse` in `backend/app/schemas/session.py`
  - [x] Registered router in `backend/app/api/v1/api.py` with prefix `/sessions`

- [x] **Task 6: Apply Supabase RLS policies** (AC: 4, 6)
  - [x] Created `backend/supabase/migrations/001_rls_policies.sql` containing RLS enables and isolation policies for all 4 tables
  - [x] SQL file header documents manual application steps (`psql $DATABASE_URL -f ...` or Supabase Studio)

- [x] **Task 7: Write Pytest tests** (AC: 2, 3, 6, 7, 8)
  - [x] Created `backend/tests/test_sessions.py`:
    - `GET /api/v1/sessions` returns only the current user's sessions (not other users')
    - `GET /api/v1/sessions/{session_id}` returns 404 for non-existent session
    - `GET /api/v1/sessions/{session_id}` returns 403 when session belongs to another user (data isolation enforcement)
    - `GET /api/v1/sessions` returns 401 without token
  - [x] Updated `backend/tests/test_bdd.py` to assert that `POST /api/v1/bdd/generate` creates a `BddFile` row (mocked db.add with instance type check)
  - [x] All tests use `app.dependency_overrides` for `get_current_user` and `get_db`, following patterns from `test_ingestion.py`

## Dev Notes

### What Already Exists — DO NOT Recreate

| Symbol | File | Notes |
|---|---|---|
| `Session` model | `backend/app/models/session.py` | id, user_id, jira_ticket_id, created_at — **complete, no changes needed** |
| `sessions` migration | `backend/alembic/versions/b37b53deea53_create_sessions_table.py` | Already applied — do NOT modify |
| `VerificationResult` model | `backend/app/models/verification_result.py` | Full model — complete |
| `verification_results` migration | `backend/alembic/versions/a9d4e72f1c83_create_verification_results_table.py` | `down_revision = "b37b53deea53"` — new migrations chain off this |
| `Base` class | `backend/app/models/base.py` | `DeclarativeBase` — import from here |
| Session creation in ingestion | `backend/app/api/v1/ingestion.py:26–31` | Session row already written at ingest time with real `current_user` ✅ |
| VerificationResult persistence | `backend/app/services/verification_service.py` | Already saves to `verification_results` table after each scenario verdict |
| `get_db()` dependency | `backend/app/core/database.py` | `AsyncSession` per request — import and use same pattern as in `ingestion.py` |
| `get_current_user` dependency | `backend/app/core/auth.py` | Returns `user_id` string from JWT — already imported in `bdd.py` |
| BDD route | `backend/app/api/v1/bdd.py:12–47` | Currently has `current_user` but no `db`. Add `db: AsyncSession = Depends(get_db)` |
| Models `__init__.py` | `backend/app/models/__init__.py` | Currently exports `Base`, `Session`, `VerificationResult` — add new models here |
| Alembic env | `backend/alembic/env.py` | Already imports from `app.models` — new models auto-included once registered |

### Key Architecture Rules — MUST FOLLOW

From `architecture.md`:

1. **Never put business logic in route handlers** — BDD file persistence should be minimal (create model, add, commit). If complex, extract to a service method.
2. **Never return user data without checking `user_id == current_user`** — Sessions API routes MUST check `session.user_id == current_user` BEFORE returning data.
3. **FastAPI data calls = TanStack React Query hooks** — No direct fetch calls in frontend components.
4. **All IDs in URLs are UUID strings** — never integers.
5. **Tables use `snake_case` plural** — `bdd_files`, `chat_messages` (not `bdd_file`, `chat_message`).
6. **Foreign keys are `{table_singular}_id`** — `session_id`, `user_id`.
7. **`AsyncSession` via `get_db()`** — session-per-request pattern, do NOT create separate DB connections.

### Alembic Migration Chain

```
b37b53deea53  ← sessions (Epic 1)
    ↓
a9d4e72f1c83  ← verification_results (Epic 2)
    ↓
<new_rev_1>   ← bdd_files (this story, Task 1)
    ↓
<new_rev_2>   ← chat_messages (this story, Task 2)
```

Do NOT use `--autogenerate` blindly — verify the generated SQL matches the intended schema before committing.

### `BddFile` Model Schema

```python
# backend/app/models/bdd_file.py
import uuid
from datetime import UTC, datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

class BddFile(Base):
    __tablename__ = "bdd_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # "generated" | "uploaded"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
```

### `ChatMessage` Model Schema

```python
# backend/app/models/chat_message.py
import uuid
from datetime import UTC, datetime
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
```

### BDD Generate Route Update Pattern

```python
# backend/app/api/v1/bdd.py — updated route signature
from app.core.database import get_db, AsyncSession
from app.models.bdd_file import BddFile
import json

@router.post("/generate", response_model=BDDGenerateResponse)
async def generate_bdd(
    request: BDDGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> BDDGenerateResponse:
    try:
        response = await generate_bdd_scenarios(
            session_id=request.session_id,
            acceptance_criteria=request.acceptance_criteria,
        )

        # Persist BDD output to bdd_files table
        bdd_file = BddFile(
            session_id=str(request.session_id),
            user_id=current_user,
            content=json.dumps(response.model_dump()),  # or Gherkin text if serializable
            source="generated",
        )
        db.add(bdd_file)
        await db.commit()

        return response
    except BDDServiceError as e:
        ...
```

> **Decision Note:** Store `content` as JSON (the full `BDDGenerateResponse` dict) rather than plain Gherkin text. This preserves scenario metadata (ids, AC traceability) needed by Story 3.5 to reconstruct the editor state. Use `json.dumps(response.model_dump())` for serialization and `json.loads(content)` for retrieval.

### Sessions API Route Pattern

```python
# backend/app/api/v1/sessions.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.session import Session
from app.schemas.session import SessionResponse

router = APIRouter()

@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> list[SessionResponse]:
    result = await db.execute(
        select(Session)
        .where(Session.user_id == current_user)
        .order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    return [SessionResponse.model_validate(s) for s in sessions]

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: str = Depends(get_current_user),
) -> SessionResponse:
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")
    return SessionResponse.model_validate(session)
```

### RLS Policy SQL Template

```sql
-- backend/supabase/migrations/001_rls_policies.sql
-- Apply after running Alembic migrations for all 4 tables.
-- Run this directly against your Supabase PostgreSQL instance:
--   psql $DATABASE_URL -f backend/supabase/migrations/001_rls_policies.sql

-- Enable RLS on all user-data tables
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bdd_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE verification_results ENABLE ROW LEVEL SECURITY;

-- sessions: users can only see/modify their own sessions
CREATE POLICY "sessions_user_isolation" ON sessions
  FOR ALL USING (auth.uid()::text = user_id);

-- bdd_files: users can only see/modify their own BDD files
CREATE POLICY "bdd_files_user_isolation" ON bdd_files
  FOR ALL USING (auth.uid()::text = user_id);

-- chat_messages: users can only see/modify their own chat history
CREATE POLICY "chat_messages_user_isolation" ON chat_messages
  FOR ALL USING (auth.uid()::text = user_id);

-- verification_results: users can only see/modify their own results
CREATE POLICY "verification_results_user_isolation" ON verification_results
  FOR ALL USING (auth.uid()::text = user_id);
```

> **Important:** RLS requires `auth.uid()` which is Supabase-specific. This SQL is NOT run via Alembic — it must be applied via Supabase Studio, `psql`, or the Supabase CLI. Document the one-time setup step in the repo README.

### Sessions Schema (Pydantic)

```python
# backend/app/schemas/session.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    jira_ticket_id: str
    created_at: datetime
```

### Registration in `api.py`

```python
# backend/app/api/v1/api.py — add sessions router
from app.api.v1 import sessions
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
```

### Story 3.2 Handoff — What Was Built

Story 3.2 delivered:
- Real JWT validation in `backend/app/core/auth.py` using PyJWT + cryptography with JWKS caching
- `Depends(get_current_user)` applied to all protected routes — ingestion, bdd, verification
- `frontend/src/middleware.ts` for Next.js route protection (redirects → `/login`)
- Axios request interceptor adding `Authorization: Bearer` to all API calls
- 93 passing tests including 9 auth tests

After Story 3.2, `current_user` in all routes is a real `user_id` UUID string from Supabase JWT — safe to store as `user_id` in DB tables.

### Story 1.6 Upload Notes

Story 1.6 implemented BDD file upload **client-side to Supabase Storage** only — it pushes the `.feature` file to a `bdd-uploads` bucket and injects content into React `SessionContext`. There is **no backend `/bdd/upload` endpoint**. Task 4 of this story creates that backend endpoint so BDD upload events are also persisted to `bdd_files` in PostgreSQL for history retrieval.

### Project Structure Notes

New files to create:
```
backend/app/models/bdd_file.py                         (new BddFile model)
backend/app/models/chat_message.py                     (new ChatMessage model)
backend/alembic/versions/<hash>_create_bdd_files_table.py
backend/alembic/versions/<hash>_create_chat_messages_table.py
backend/app/api/v1/sessions.py                         (new sessions router)
backend/app/schemas/session.py                         (new SessionResponse schema)
backend/supabase/migrations/001_rls_policies.sql       (run manually, not via Alembic)
backend/tests/test_sessions.py                         (new)
```

Files to modify:
```
backend/app/models/__init__.py                         (add BddFile, ChatMessage exports)
backend/app/api/v1/bdd.py                              (add db dependency + BddFile persistence)
backend/app/api/v1/api.py                              (register sessions router)
backend/tests/test_bdd.py                              (assert bdd_files row created on generate)
```

No frontend changes in this story — UI for session history is Story 3.5.

### References

- Story 3.3 AC: [epics.md](_bmad-output/planning-artifacts/epics.md#story-33-session-persistence--database-schema)
- Existing `Session` model: [session.py](backend/app/models/session.py)
- Existing `VerificationResult` model: [verification_result.py](backend/app/models/verification_result.py)
- Sessions migration (chain base): [a9d4e72f1c83...](backend/alembic/versions/a9d4e72f1c83_create_verification_results_table.py)
- `get_db()` usage reference: [ingestion.py](backend/app/api/v1/ingestion.py)
- BDD generate route: [bdd.py](backend/app/api/v1/bdd.py)
- Verification route (db usage pattern): [verification.py](backend/app/api/v1/verification.py)
- API router registration: [api.py](backend/app/api/v1/api.py)
- Models `__init__` (add new models here): [__init__.py](backend/app/models/__init__.py)
- Auth test patterns: [test_auth.py](backend/tests/test_auth.py)
- FR31: session persistence, FR34: RLS isolation, NFR-S6: RLS enforcement

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

No blockers encountered. Two test assertion fixes: 404/403 `HTTPException` responses are wrapped by the global error envelope handler into `{"error": "HTTP_ERROR", "message": "...", "code": ...}` — tests updated to check `data["message"]` instead of `data["detail"]`.

### Completion Notes List

- Created `BddFile` and `ChatMessage` SQLAlchemy models (UUID PK, session_id/user_id indexed) following existing `VerificationResult` model pattern.
- Authored Alembic migrations manually (autogenerate skipped per story guidance) — migration chain: `b37b53deea53` → `a9d4e72f1c83` → `c1f2a3b4d5e6` → `d2e3f4a5b6c7`.
- `generate_bdd` route now persists full `BDDGenerateResponse` as JSON to `bdd_files` (preserves scenario metadata + AC traceability for Story 3.5 history retrieval).
- Created `POST /api/v1/bdd/upload` endpoint (multipart) since Story 1.6 upload was entirely client-side to Supabase Storage — backend persistence was absent.
- Sessions API (`GET /sessions`, `GET /sessions/{id}`) enforces `user_id == current_user` at application layer (FastAPI) before returning data. 404/403 HTTPExceptions are raised explicitly.
- RLS SQL file created at `backend/supabase/migrations/001_rls_policies.sql` — must be applied manually against Supabase PostgreSQL (not via Alembic). File header documents `psql` command.
- 100 tests passing (93 pre-existing + 7 new). No regressions.

### File List

backend/app/models/bdd_file.py (new)
backend/app/models/chat_message.py (new)
backend/app/models/__init__.py (modified — added BddFile, ChatMessage exports)
backend/alembic/versions/c1f2a3b4d5e6_create_bdd_files_table.py (new)
backend/alembic/versions/d2e3f4a5b6c7_create_chat_messages_table.py (new)
backend/app/api/v1/bdd.py (modified — added db dependency, BddFile persistence, upload endpoint)
backend/app/schemas/bdd.py (modified — added BDDUploadResponse)
backend/app/api/v1/sessions.py (new)
backend/app/schemas/session.py (new)
backend/app/api/v1/api.py (modified — registered sessions router)
backend/supabase/migrations/001_rls_policies.sql (new)
backend/tests/test_sessions.py (new)
backend/tests/test_bdd.py (modified — mock db injection, BddFile persistence assertions, upload tests, merged duplicate test)

## Senior Developer Review (AI)

**Outcome:** Changes Requested → Fixed
**Review Date:** 2026-04-11
**Reviewer Model:** claude-sonnet-4-6

### Action Items (all resolved)

- [x] **[High]** Zero tests for `POST /api/v1/bdd/upload` — AC3 persistence unverified [backend/tests/test_bdd.py]
- [x] **[High]** `upload_bdd` writes `bdd_files` without verifying `session_id` belongs to `current_user` [backend/app/api/v1/bdd.py:88]
- [x] **[Medium]** `test_generate_bdd_model_failure` and `test_generate_bdd_does_not_persist_on_service_failure` were duplicate tests covering the same code path [backend/tests/test_bdd.py:86-139]
- [x] **[Medium]** No file size limit in `upload_bdd` — full memory read with no cap [backend/app/api/v1/bdd.py:85]
- [x] **[Medium]** `file: UploadFile = ...` non-standard FastAPI syntax; should be `File(...)` [backend/app/api/v1/bdd.py:68]
- [x] **[Medium]** `session_id: str = Form(...)` — no UUID validation on upload form param [backend/app/api/v1/bdd.py:67]
- [x] **[Medium]** `from fastapi.responses import JSONResponse` imported inside `except` block [backend/app/api/v1/bdd.py:53]

## Change Log

- 2026-04-11: Implemented Story 3.3 — Session Persistence & Database Schema. Created BddFile and ChatMessage models with Alembic migrations, persisted BDD generation output to bdd_files, added POST /bdd/upload endpoint, created Sessions API routes with user isolation enforcement, created Supabase RLS policy SQL file. 100 tests passing.
- 2026-04-11: Code review fixes — added session ownership validation to upload endpoint, 5 upload tests added (success, 404-not-found, 422-wrong-extension, 413-oversized, 401-unauth), file size limit (512 KB), `File(...)` parameter, UUID form validation, top-level JSONResponse import, merged duplicate test. 104 tests passing.
