# Story 3.4: Supabase Storage Setup

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want my generated reports, uploaded feature files, and test artifacts to be stored persistently,
So that I can download them at any time and they're not lost between sessions.

## Acceptance Criteria

1. **Given** the Supabase Storage service is configured
   **When** the backend `StorageService` in `app/services/storage_service.py` is instantiated
   **Then** it wraps all Supabase Storage SDK operations (upload, download, delete, list) behind a clean interface (FR42, FR43, FR44)

2. **And** three storage buckets are in use: `reports`, `feature-files`, `artifacts` — declared as constants `BUCKET_REPORTS`, `BUCKET_FEATURE_FILES`, `BUCKET_ARTIFACTS`

3. **And** bucket-level storage policies enforce per-`user_id` access — a user can only read/write their own files (storage paths are scoped to `{user_id}/...`)

4. **And** when a `.feature` file is uploaded via `POST /api/v1/bdd/upload`
   **Then** the file content is persisted both to PostgreSQL (`bdd_files` table — already done in Story 3.3) **AND** the raw file bytes are uploaded to the `feature-files` Supabase Storage bucket at path `{user_id}/{session_id}/{filename}`

5. **And** all file operations in route handlers and services go through `StorageService` — never direct Supabase Storage SDK calls

6. **And** if Supabase Storage is not configured (missing `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` env vars), the service logs a warning and the upload endpoint continues to work (PostgreSQL persistence succeeds, Supabase Storage upload is skipped with a warning log)

7. **And** Pytest tests verify `StorageService` upload, download, delete, and list methods using a mocked supabase client

## Tasks / Subtasks

- [x] **Task 1: Add `supabase` Python SDK to dependencies** (AC: 1)
  - [x] Add `supabase>=2.0.0` to `dependencies` in `backend/pyproject.toml`
  - [x] Verify `supabase` package is importable in the backend environment

- [x] **Task 2: Verify and finalise `StorageService` implementation** (AC: 1, 2, 3, 5, 6)
  - [x] Read the existing `backend/app/services/storage_service.py` in full
  - [x] Confirm `upload_file`, `download_file`, `delete_file`, `list_files` all handle the `not self._client` path gracefully with `StorageServiceError`
  - [x] Confirm bucket constants `BUCKET_REPORTS`, `BUCKET_FEATURE_FILES`, `BUCKET_ARTIFACTS` are exported
  - [x] Confirm singleton `storage_service` instance is exported at module level
  - [x] Fix `upload_file` if needed: `upsert` as `"true"` string is correct for supabase-py v2 (header value); removed unused `result =` assignment (F841)
  - [x] Update the class docstring to remove "stub" language — production implementation
  - [x] No architectural changes needed — the existing structure is correct

- [x] **Task 3: Wire `StorageService` into `POST /api/v1/bdd/upload`** (AC: 4, 5, 6)
  - [x] In `backend/app/api/v1/bdd.py`, import `storage_service`, `BUCKET_FEATURE_FILES`, `StorageServiceError` from `app.services.storage_service`
  - [x] After the `BddFile` row is committed to PostgreSQL, attempt to upload raw bytes to Supabase Storage at path `{session_id}/{filename}`
  - [x] Import `logging` and create `logger = logging.getLogger(__name__)` at top of `bdd.py`
  - [x] The upload to PostgreSQL ALWAYS succeeds — storage failure is non-fatal (logged as WARNING)
  - [x] Reuse already-read `raw` bytes — do NOT call `file.read()` again

- [x] **Task 4: Create Supabase Storage bucket policies SQL** (AC: 3)
  - [x] Created `backend/supabase/migrations/002_storage_bucket_policies.sql`
  - [x] INSERT/SELECT/DELETE policies per bucket scoped to `auth.uid()::text = (storage.foldername(name))[1]`
  - [x] File header documents manual application steps (NOT via Alembic)
  - [x] Bucket creation prerequisite documented (manual step in Supabase Studio)

- [x] **Task 5: Write Pytest tests for `StorageService`** (AC: 7)
  - [x] Created `backend/tests/test_storage_service.py` with 21 tests
  - [x] Tests mock `StorageService._client` directly (no real supabase client needed)
  - [x] Covered: upload (success, not-configured, sdk-error, path-scoping, no-user-id), download (success, scoping, not-configured, sdk-error), delete (success, not-configured, sdk-error), list (success, empty-prefix, not-configured, sdk-error), bucket constants, error attributes, is_available

- [x] **Task 6: Update `bdd.py` upload tests for storage integration** (AC: 4, 6)
  - [x] Updated `test_upload_bdd_success_persists_with_source_uploaded` to patch `app.api.v1.bdd.storage_service.upload_file` and assert correct bucket, path, and user_id
  - [x] Added `test_upload_bdd_storage_failure_still_succeeds` — raises `StorageServiceError`, asserts HTTP 200 and PostgreSQL commit still called
  - [x] 126 tests total passing (104 pre-existing + 22 new) — zero regressions

## Dev Notes

### What Already Exists — DO NOT Recreate

| Symbol | File | Notes |
|---|---|---|
| `StorageService` class | `backend/app/services/storage_service.py` | All 4 methods implemented (stub from Epic 1). Needs `supabase` package + minor fixes only |
| `BUCKET_REPORTS`, `BUCKET_FEATURE_FILES`, `BUCKET_ARTIFACTS` | `backend/app/services/storage_service.py` | Already defined as module-level constants |
| `storage_service` singleton | `backend/app/services/storage_service.py` | Already exported at bottom of module |
| `StorageServiceError` | `backend/app/services/storage_service.py` | Already defined with `message` and `code` attributes |
| `POST /api/v1/bdd/upload` | `backend/app/api/v1/bdd.py:69–116` | Story 3.3 implementation — persists to PostgreSQL. Task 3 adds Supabase Storage call |
| `raw` bytes variable | `backend/app/api/v1/bdd.py:98` | Already reads file bytes before commit — reuse in Task 3 |
| Supabase env vars | `backend/app/core/config.py` | `supabase_url`, `supabase_service_role_key`, `supabase_anon_key` already declared in `Settings` |
| RLS policies SQL | `backend/supabase/migrations/001_rls_policies.sql` | Story 3.3 — PostgreSQL RLS. Task 4 creates **002** for Storage bucket policies (separate concept) |

### Supabase Python SDK Notes

The `supabase` package (supabase-py v2.x) must be installed. Import pattern already correct in `storage_service.py`:
```python
from supabase import create_client
client = create_client(supabase_url, service_role_key)
```

**File options in supabase-py v2.x:** The `upload` method accepts `file_options` as a dict:
```python
client.storage.from_(bucket).upload(
    path=scoped_path,
    file=file_data,
    file_options={"content-type": content_type, "upsert": True}  # upsert = bool
)
```
Check the existing `storage_service.py` — if `upsert` is `"true"` (string), update it to `True` (bool). The key `"content-type"` with hyphen is correct for supabase-py v2.

### Bucket Policy SQL Template (Task 4)

Supabase Storage bucket policies use a different SQL syntax from table RLS. The SQL below uses the Supabase Storage policy functions:

```sql
-- backend/supabase/migrations/002_storage_bucket_policies.sql
-- =============================================================
-- Supabase Storage Bucket Policies for qe-agent-v2
-- =============================================================
-- IMPORTANT: This file is NOT applied via Alembic.
-- Apply manually via Supabase Dashboard → Storage → Policies, OR:
--   psql $DATABASE_URL -f backend/supabase/migrations/002_storage_bucket_policies.sql
--
-- Prerequisites: Buckets must already exist in Supabase Dashboard:
--   - reports
--   - feature-files
--   - artifacts
-- Create buckets manually in Supabase Studio → Storage → New Bucket (private, not public).
-- =============================================================

-- Enable RLS on storage.objects (Supabase does this by default, but explicit is safer)
ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;

-- ─── reports bucket: per-user read/write ───────────────────────────────────
CREATE POLICY "reports_user_insert" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'reports' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "reports_user_select" ON storage.objects
  FOR SELECT USING (
    bucket_id = 'reports' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "reports_user_delete" ON storage.objects
  FOR DELETE USING (
    bucket_id = 'reports' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

-- ─── feature-files bucket: per-user read/write ─────────────────────────────
CREATE POLICY "feature_files_user_insert" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'feature-files' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "feature_files_user_select" ON storage.objects
  FOR SELECT USING (
    bucket_id = 'feature-files' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "feature_files_user_delete" ON storage.objects
  FOR DELETE USING (
    bucket_id = 'feature-files' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

-- ─── artifacts bucket: per-user read/write ─────────────────────────────────
CREATE POLICY "artifacts_user_insert" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'artifacts' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "artifacts_user_select" ON storage.objects
  FOR SELECT USING (
    bucket_id = 'artifacts' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );

CREATE POLICY "artifacts_user_delete" ON storage.objects
  FOR DELETE USING (
    bucket_id = 'artifacts' AND
    auth.uid()::text = (storage.foldername(name))[1]
  );
```

**How the path scoping works:** `storage_service.upload_file()` prepends `user_id` to every path: `scoped_path = f"{user_id}/{path}"`. The bucket policy checks `(storage.foldername(name))[1]` which extracts the first folder segment (the `user_id`). This enforces that only the file owner can read/write their files.

### Storage Upload Pattern in `bdd.py` (Task 3)

```python
# backend/app/api/v1/bdd.py — add to upload_bdd after db.commit()

import logging
from app.services.storage_service import (
    BUCKET_FEATURE_FILES,
    StorageServiceError,
    storage_service,
)

logger = logging.getLogger(__name__)

# ... (after BddFile commit) ...
    storage_path = f"{session_id}/{file.filename}"
    try:
        await storage_service.upload_file(
            bucket=BUCKET_FEATURE_FILES,
            path=storage_path,
            file_data=raw,
            content_type="text/plain",
            user_id=current_user,
        )
    except StorageServiceError as e:
        logger.warning(
            "Supabase Storage upload skipped (PostgreSQL persisted): %s", e.message
        )

    return BDDUploadResponse(session_id=str(session_id), content=content)
```

**Important:** `raw` is already held in memory from `raw = await file.read()` at line 98. Do NOT call `file.read()` again — the file pointer is exhausted. Pass `raw` directly.

### StorageService Test Pattern (Task 5)

```python
# backend/tests/test_storage_service.py
from unittest.mock import MagicMock, patch
import pytest
from app.services.storage_service import (
    BUCKET_FEATURE_FILES,
    StorageService,
    StorageServiceError,
)

def _make_service_with_mock_client():
    """Create StorageService with a mocked supabase client."""
    service = StorageService.__new__(StorageService)
    service._supabase_url = "https://test.supabase.co"
    service._service_role_key = "test-key"
    mock_client = MagicMock()
    service._client = mock_client
    return service, mock_client

@pytest.mark.asyncio
async def test_upload_file_success():
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().upload.return_value = {}

    result = await service.upload_file(
        bucket=BUCKET_FEATURE_FILES,
        path="session-abc/test.feature",
        file_data=b"Feature: test",
        content_type="text/plain",
        user_id="user-123",
    )

    assert result == "user-123/session-abc/test.feature"
    mock_client.storage.from_().upload.assert_called_once()

@pytest.mark.asyncio
async def test_upload_file_not_configured():
    service = StorageService.__new__(StorageService)
    service._client = None

    with pytest.raises(StorageServiceError) as exc_info:
        await service.upload_file(
            bucket=BUCKET_FEATURE_FILES,
            path="test.feature",
            file_data=b"",
            user_id="user-123",
        )
    assert exc_info.value.code == "STORAGE_NOT_CONFIGURED"
```

### Alembic Migration Chain (No Change)

Story 3.4 adds NO Alembic migrations. The migration chain from Story 3.3 is final for Epic 3:
```
b37b53deea53  ← sessions (Epic 1)
    ↓
a9d4e72f1c83  ← verification_results (Epic 2)
    ↓
c1f2a3b4d5e6  ← bdd_files (Story 3.3)
    ↓
d2e3f4a5b6c7  ← chat_messages (Story 3.3)
```

### Story 3.3 Handoff — What Was Built

Story 3.3 delivered:
- `BddFile` and `ChatMessage` SQLAlchemy models with Alembic migrations
- `POST /api/v1/bdd/generate` now persists full BDDGenerateResponse JSON to `bdd_files`
- `POST /api/v1/bdd/upload` accepts multipart `.feature` file, validates session ownership, persists to `bdd_files`, enforces 512 KB limit
- `GET /api/v1/sessions` and `GET /api/v1/sessions/{session_id}` routes with user isolation
- Supabase RLS SQL in `backend/supabase/migrations/001_rls_policies.sql`
- 104 tests passing (all green)

After Story 3.3, `current_user` in all routes is a real JWT user_id UUID string from Supabase.

### Non-Fatal Storage Pattern

Supabase Storage is a **secondary** persistence mechanism in this story. PostgreSQL is always the source of truth for BDD content. Storage failure must never break the API endpoint. The pattern is:
1. Commit to PostgreSQL (primary, always required — raise on failure)
2. Attempt Supabase Storage upload (secondary, non-fatal — log WARNING on failure, return 200)

This ensures the API works even if the Supabase Storage env vars are not configured in local dev.

### Environment Variables Required for Full Storage

```bash
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
```

These are already declared in `backend/app/core/config.py:Settings` as `supabase_url` and `supabase_service_role_key`. No new env vars needed.

### Project Structure Notes

**New files to create:**
```
backend/supabase/migrations/002_storage_bucket_policies.sql   (apply manually, not Alembic)
backend/tests/test_storage_service.py                          (new)
```

**Files to modify:**
```
backend/pyproject.toml                                         (add supabase>=2.0.0 dependency)
backend/app/services/storage_service.py                       (remove "stub" docstring, fix upsert bool if needed)
backend/app/api/v1/bdd.py                                     (add storage_service call in upload_bdd)
backend/tests/test_bdd.py                                     (add storage mock to upload tests)
```

**No frontend changes in this story** — the frontend already does client-side uploads in Story 1.6. This story only wires the backend service layer.

### Architecture Compliance Checklist

- [ ] `StorageService` is the ONLY place that calls Supabase Storage SDK — no direct SDK calls anywhere else
- [ ] All storage paths are user-scoped via `storage_service.upload_file(user_id=current_user)`
- [ ] Storage failure does NOT propagate as HTTP 500 from upload endpoint
- [ ] `supabase` package imported conditionally (already done in stub — `try/except ImportError` or lazy import)
- [ ] All IDs in storage paths are strings (UUID strings), consistent with architecture rule

### References

- Story 3.4 AC: [epics.md](_bmad-output/planning-artifacts/epics.md#story-34-supabase-storage-setup)
- StorageService stub: [storage_service.py](backend/app/services/storage_service.py)
- BDD upload route: [bdd.py:69–116](backend/app/api/v1/bdd.py#L69-L116)
- Backend config (supabase env vars): [config.py](backend/app/core/config.py)
- Existing RLS SQL: [001_rls_policies.sql](backend/supabase/migrations/001_rls_policies.sql)
- Story 3.3 (handoff): [3-3-session-persistence-and-database-schema.md](_bmad-output/implementation-artifacts/3-3-session-persistence-and-database-schema.md)
- pyproject.toml (add supabase dep): [pyproject.toml](backend/pyproject.toml)
- FR42: reports stored in Supabase Storage, FR43: uploaded .feature files stored, FR44: test artifacts stored

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

No blockers encountered. One pre-existing F841 (`result =` unused assignment in `upload_file`) fixed in `storage_service.py`. The supabase-py v2 SDK uses `"upsert": "true"` (string, not bool) as a header value — no change needed there. The existing stub structure was already correct for production use.

### Completion Notes List

- Added `supabase>=2.0.0` to `backend/pyproject.toml` dependencies.
- Updated `StorageService` class docstring — removed "stub" language, replaced with production description of singleton, graceful unavailability, and user-id path scoping.
- Fixed F841 lint error: removed unused `result =` from `upload_file` (returned `scoped_path` directly).
- Wired `StorageService` into `POST /api/v1/bdd/upload`: imports `storage_service`, `BUCKET_FEATURE_FILES`, `StorageServiceError`; calls `upload_file` after PostgreSQL commit; non-fatal (logs WARNING, returns 200 regardless). Reuses already-read `raw` bytes.
- Created `backend/supabase/migrations/002_storage_bucket_policies.sql` with INSERT/SELECT/DELETE policies for all three buckets (`reports`, `feature-files`, `artifacts`) scoped to `auth.uid()::text = (storage.foldername(name))[1]`. Documented manual application (not Alembic).
- Created `backend/tests/test_storage_service.py` with 21 tests covering all 4 operations, path scoping, not-configured error, SDK error wrapping, bucket constants, and `StorageServiceError` attributes.
- Updated `test_bdd.py`: success test now patches `storage_service.upload_file` and asserts correct bucket/path/user_id; added `test_upload_bdd_storage_failure_still_succeeds` to verify non-fatal storage failure pattern.
- 126 total tests passing (zero regressions from 104 pre-existing).

### File List

backend/pyproject.toml (modified — added supabase>=2.0.0)
backend/app/services/storage_service.py (modified — updated docstring, fixed F841)
backend/app/api/v1/bdd.py (modified — added logging, storage_service imports and upload call in upload_bdd)
backend/supabase/migrations/002_storage_bucket_policies.sql (new)
backend/tests/test_storage_service.py (new — 21 tests)
backend/tests/test_bdd.py (modified — storage mock in success test, new storage-failure test, new non-UTF-8 test)

## Senior Developer Review (AI)

**Outcome:** Changes Requested → Fixed
**Review Date:** 2026-04-11
**Reviewer Model:** claude-sonnet-4-6

### Action Items (all resolved)

- [x] **[High]** Sync supabase SDK calls inside `async def` methods block the event loop — wrapped all 4 SDK calls with `asyncio.to_thread()` [backend/app/services/storage_service.py:105,144,176,210]
- [x] **[High]** `raw.decode("utf-8")` raises unhandled `UnicodeDecodeError` → HTTP 500 for non-UTF-8 files — wrapped in try/except, raises HTTP 422, test added [backend/app/api/v1/bdd.py:112]
- [x] **[Medium]** `StorageService.__init__` `except Exception:` drops error message — added `as e` and passes `e` to logger.warning [backend/app/services/storage_service.py:61]
- [x] **[Medium]** `list_files` produces trailing-slash path `"{user_id}/"` when prefix is empty — fixed with `.rstrip("/")`, test assertion updated [backend/app/services/storage_service.py:207]

## Change Log

- 2026-04-11: Implemented Story 3.4 — Supabase Storage Setup. Added supabase SDK dependency, finalized StorageService (removed stub docstring, fixed F841), wired storage upload into POST /bdd/upload (non-fatal pattern), created bucket policies SQL, 21 StorageService tests + 1 storage-failure BDD test. 126 total tests passing.
- 2026-04-11: Code review fixes — wrapped all sync SDK calls with asyncio.to_thread() (event loop safety), caught UnicodeDecodeError in upload_bdd (returns 422 not 500), logged init exception details, fixed list_files trailing-slash bug. 1 new test (non-UTF-8 upload). 127 tests passing.
