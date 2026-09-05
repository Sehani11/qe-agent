# Story 2.2: GitHub Code Fetching (All 3 Modes)

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): The `/verification/fetch` endpoint, `fetch_github_code` dispatcher and `fetch_full_repo` this story built were REMOVED with the legacy two-step flow — the UI is agentic-only (Story 2.5). `fetch_exact_files_resolved` and `fetch_pull_request` survive as the agentic path's evidence fetchers, now with slash-ref resolution, PR pagination, and head-SHA links. Read this story as history, not as the current API surface.

## Story

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want the system to fetch the relevant code from GitHub based on my selected mode,
so that the correct code is available for LLM-based verification without me having to manually copy-paste it.

## Acceptance Criteria

1. **Given** the user has selected "Exact File Paths" and entered one or more valid GitHub blob URLs (one per line)
   **When** a POST request is submitted to `/api/v1/verification/fetch`
   **Then** the FastAPI GitHub service fetches each specified file's raw content from the GitHub API (FR20)

2. **Given** "Full Repository" mode is selected with a valid GitHub repo URL (e.g. `https://github.com/org/repo`)
   **When** the verification fetch request is submitted
   **Then** the GitHub service fetches the full file tree and content of all text files in the repository, capped at 100 files (FR21)

3. **Given** "Pull Request" mode is selected with a valid PR URL (e.g. `https://github.com/org/repo/pull/42`)
   **When** the verification fetch request is submitted
   **Then** the GitHub service fetches the PR file list and their diff/patch content from the GitHub API (FR22)

4. **And** if any GitHub fetch fails (file not found, invalid URL, rate limit, API error, network error)
   **Then** the API returns a JSON error envelope: `{"error": "GITHUB_FETCH_FAILED", "message": "<human-readable>", "code": 422}`
   **And** the frontend displays the error message without clearing `bddContent` or session state (FR26, FR27, NFR-R1, NFR-S7)

5. **And** GitHub API 429 responses trigger automatic retry with exponential backoff (1 s → 2 s → 4 s, max 3 retries) before surfacing a user-facing error (NFR-R2)

6. **And** the "Verify" button in `GitHubSourceSelector` shows a loading state while the fetch is in flight and re-enables once it resolves or fails

7. **And** no LLM call is made in this story — fetched code is returned to the frontend for use in Story 2.3

## Tasks / Subtasks

- [x] **Task 1: Create verification Pydantic schemas** (AC: 1, 2, 3)
  - [x] Create `backend/app/schemas/verification.py`
  - [x] Add `VerificationFetchRequest` model: `session_id: str`, `mode: Literal["exact_files", "full_repo", "pull_request"]`, `github_input: str`
  - [x] Add `FetchedFile` model: `path: str`, `content: str`
  - [x] Add `VerificationFetchResponse` model: `session_id: str`, `mode: str`, `fetched_files: list[FetchedFile]`

- [x] **Task 2: Implement `github_service.py`** (AC: 1, 2, 3, 4, 5)
  - [x] Create `backend/app/services/github_service.py`
  - [x] Define `GitHubServiceError(Exception)` with `message: str` and `code: str = "GITHUB_FETCH_FAILED"`
  - [x] Implement `_github_get(url, pat) → dict` — async httpx GET with `Authorization: Bearer {pat}` header and 429 exponential backoff (max 3 retries: 1 s, 2 s, 4 s via `asyncio.sleep`)
  - [x] Implement `_parse_github_blob_url(url: str) → tuple[str, str, str, str]` returning `(owner, repo, ref, path)` from `https://github.com/{owner}/{repo}/blob/{ref}/{path}`
  - [x] Implement `_parse_repo_url(url: str) → tuple[str, str]` returning `(owner, repo)` from `https://github.com/{owner}/{repo}`
  - [x] Implement `_parse_pr_url(url: str) → tuple[str, str, int]` returning `(owner, repo, pr_number)` from `https://github.com/{owner}/{repo}/pull/{number}`
  - [x] Implement `fetch_exact_files(paths_input: str, pat: str) → list[FetchedFile]`
    - [x] Split `paths_input` by newline; strip and skip blanks
    - [x] Parse each line as a GitHub blob URL via `_parse_github_blob_url`
    - [x] Call `GET https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}`
    - [x] Decode base64 `content` field → UTF-8 string
    - [x] Raise `GitHubServiceError` for 404, 401, or unexpected status codes
  - [x] Implement `fetch_full_repo(repo_url: str, pat: str) → list[FetchedFile]`
    - [x] Parse owner/repo from `repo_url` via `_parse_repo_url`
    - [x] Call `GET https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1`
    - [x] Filter tree items: `type == "blob"` only; skip files with `size > 524288` (512 KB); skip paths ending with binary extensions (`.png`, `.jpg`, `.gif`, `.svg`, `.ico`, `.woff`, `.woff2`, `.ttf`, `.eot`, `.pdf`, `.zip`, `.gz`, `.tar`, `.bin`, `.exe`, `.lock`)
    - [x] Take first 100 qualifying files (ordered as returned by API)
    - [x] Fetch each file content via `GET https://api.github.com/repos/{owner}/{repo}/contents/{path}`; decode base64
    - [x] Return list of `FetchedFile`
  - [x] Implement `fetch_pull_request(pr_url: str, pat: str) → list[FetchedFile]`
    - [x] Parse owner/repo/pr_number via `_parse_pr_url`
    - [x] Call `GET https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files`
    - [x] For each file in response: use `patch` field as content (the diff); `filename` as path
    - [x] Skip files with `status == "removed"` (no content to verify against)
    - [x] Return list of `FetchedFile(path=file["filename"], content=file.get("patch", ""))`
  - [x] Implement public entrypoint `fetch_github_code(mode, github_input, pat) → list[FetchedFile]` — dispatches to the three mode functions; raises `GitHubServiceError` on any unhandled exception

- [x] **Task 3: Create `api/v1/verification.py` route handler** (AC: 1, 2, 3, 4)
  - [x] Create `backend/app/api/v1/verification.py`
  - [x] Import `github_service`, `schemas.verification`, `core.config.settings`
  - [x] Define `router = APIRouter()`
  - [x] Implement `POST /fetch` handler:
    - [x] Accept `VerificationFetchRequest` body
    - [x] Call `github_service.fetch_github_code(mode, github_input, settings.github_pat)`
    - [x] Return `VerificationFetchResponse` as `JSONResponse` (bypasses global HTTPException handler to preserve architecture error envelope)
    - [x] Catch `GitHubServiceError` → return `JSONResponse(status_code=422, content={"error": ..., "message": ..., "code": 422})`
  - [x] **No auth check** — DEV_USER_ID stub is still in place for all of Epic 2 (auth is wired in Epic 3)

- [x] **Task 4: Register the verification router** (AC: 1)
  - [x] Edit `backend/app/api/v1/api.py`
  - [x] Import and include `verification.router` with prefix `"/verification"` and tag `"verification"`

- [x] **Task 5: Create `useVerification.ts` frontend hook** (AC: 6, 7)
  - [x] Create `frontend/src/lib/hooks/useVerification.ts`
  - [x] Add `FetchedFile` and `VerificationFetchResponse` types to `frontend/src/lib/types/verification.ts`
  - [x] Implement `useVerification()` — TanStack `useMutation` calling `POST /verification/fetch`
  - [x] Return typed `FetchedFile[]` on success

- [x] **Task 6: Wire `onVerify` in the session page** (AC: 6, 4)
  - [x] Edit `frontend/src/app/session/[sessionId]/page.tsx`
  - [x] Import `useVerification` hook and `isVerifying` from `useSessionContext`
  - [x] In `handleVerify`: call `verifyMutation.mutate({session_id, mode: verificationMode, github_input: githubInput})`
  - [x] On success: store `fetched_files` in `SessionContext` via `setFetchedFiles` — Story 2.3 will consume them
  - [x] On error: call `setGlobalError(...)` — do NOT clear `bddContent`, `verificationMode`, or `githubInput`
  - [x] Pass `isVerifying={isVerifying}` and `onVerify={handleVerify}` to `<GitHubSourceSelector />`
  - [x] `setIsVerifying` called in `handleVerify` to keep context `isVerifying` in sync

- [x] **Task 7: Add `fetchedFiles` to `SessionContext`** (for Story 2.3 consumption)
  - [x] Edit `frontend/src/context/SessionContext.tsx`
  - [x] Add `fetchedFiles: FetchedFile[]` and `setFetchedFiles: (files: FetchedFile[]) => void` to the context interface and provider
  - [x] Reset `fetchedFiles` to `[]` in `handleIngestTrigger` alongside the existing M2 reset

- [x] **Task 8: Update `GitHubSourceSelector` placeholder for Exact Files mode** (AC: 1)
  - [x] Edit `frontend/src/components/pipeline/GitHubSourceSelector.tsx`
  - [x] Changed `"exact_files"` placeholder to `"One GitHub file URL per line, e.g. https://github.com/org/repo/blob/main/src/auth/routes.py"`

- [x] **Task 9: Write backend Pytest tests** (AC: 1, 2, 3, 4, 5)
  - [x] Create `backend/tests/test_verification.py`
  - [x] Mock `httpx.AsyncClient` to simulate GitHub API responses
  - [x] Test exact files: happy path → returns list of `FetchedFile`
  - [x] Test exact files: 404 → `GitHubServiceError` raised
  - [x] Test exact files: invalid URL format → `GitHubServiceError` raised
  - [x] Test full repo: happy path → returns capped file list; binary extensions filtered out
  - [x] Test full repo: invalid repo URL → `GitHubServiceError`
  - [x] Test PR fetch: happy path → returns file list with patch content; removed files excluded
  - [x] Test PR fetch: invalid PR URL → `GitHubServiceError`
  - [x] Test 429 retry: first 2 calls return 429, third returns 200 → retries succeed
  - [x] Test 429 retry exhausted: all 3 retries return 429 → `GitHubServiceError` raised

## Dev Notes

### Epic 2 Context — DEV_USER_ID Stub

This is Epic 2 (GitHub Code Verification). **No auth middleware is applied to verification routes.** The `DEV_USER_ID` stub from Epic 1 is still in place. Do **not** add `Depends(get_current_user)` to any verification route — that comes in Epic 3.

### What Already Exists (from Story 2.1)

| File | Relevant Content |
|---|---|
| `frontend/src/lib/types/verification.ts` | `VerificationMode` type + `GitHubSourceInput` interface — extend this file, do NOT rewrite |
| `frontend/src/context/SessionContext.tsx` | `verificationMode`, `githubInput`, `isVerifying`, `setIsVerifying` already exist |
| `frontend/src/components/pipeline/GitHubSourceSelector.tsx` | `onVerify: () => void` and `isVerifying: boolean` props already defined — no component changes needed beyond the placeholder text |
| `frontend/src/app/session/[sessionId]/page.tsx` | `<GitHubSourceSelector onVerify={() => {}} isVerifying={false} />` — replace the stub with real handler |
| `backend/app/core/config.py` | `settings.github_pat: str` already declared (line 46) |

### Architecture-Mandated Patterns — ALL MUST BE FOLLOWED

1. **No business logic in route handlers** — `verification.py` handler calls `github_service.fetch_github_code()` and nothing else
2. **No raw fetch/axios in components** — `page.tsx` uses `useVerification()` hook only
3. **FastAPI calls = TanStack React Query hooks only** — the `useMutation` hook wraps `apiClient.post()`
4. **No `any` in TypeScript** — `FetchedFile` and `VerificationFetchResponse` must be fully typed
5. **Error envelope format**: `{"error": "GITHUB_FETCH_FAILED", "message": "...", "code": 422}` — matches architecture rule (line 172–175 of architecture.md)
6. **Long-running operations must expose loader state** — `isVerifying={verifyMutation.isPending}` passed to `GitHubSourceSelector`
7. **Never leave a long-running action without user feedback** — verify button shows "Verifying…" during fetch

### GitHub API Details

**Base URL:** `https://api.github.com`

**Auth header:** `Authorization: Bearer {GITHUB_ACCESS_TOKEN}` (modern form; `token` prefix also accepted but Bearer is preferred)

**Rate limits (unauthenticated vs authenticated):**
- Unauthenticated: 60 req/hr — will fail for full-repo fetches
- Authenticated (PAT): 5,000 req/hr — sufficient for MVP

**Exact Files — API call per file:**
```
GET https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}
Headers: Authorization: Bearer {pat}, Accept: application/vnd.github+json
Response: { "content": "<base64>", "encoding": "base64", "name": "...", "path": "..." }
```
Decode: `base64.b64decode(response["content"].replace("\n", "")).decode("utf-8")`

**Full Repository — two-step:**
```
Step 1: GET https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1
Response: { "tree": [{"path": "...", "type": "blob", "size": 1234}, ...], "truncated": bool }
```
If `truncated == true`, warn in the returned file list but continue with available files.
```
Step 2 (per file): GET https://api.github.com/repos/{owner}/{repo}/contents/{path}
```

**Pull Request — single call:**
```
GET https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files
Headers: Authorization: Bearer {pat}, Accept: application/vnd.github+json
Response: array of { "filename": "...", "status": "added|modified|removed", "patch": "..." }
```
Use `patch` as content (the unified diff). `patch` can be `None` for binary files — skip those.

**URL Parsing Examples:**
```
Blob URL:  https://github.com/org/repo/blob/main/src/auth.py
           → owner=org, repo=repo, ref=main, path=src/auth.py

Repo URL:  https://github.com/org/repo
           → owner=org, repo=repo

PR URL:    https://github.com/org/repo/pull/42
           → owner=org, repo=repo, pr_number=42
```

**Binary file extensions to skip in full-repo mode:**
```python
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tar", ".bz2",
    ".bin", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".class",
    ".lock",  # e.g. package-lock.json (too large, not useful for verification)
}
```

**Exponential backoff implementation (3 retries):**
```python
import asyncio

async def _github_get_with_retry(client: httpx.AsyncClient, url: str, headers: dict) -> httpx.Response:
    for attempt in range(4):  # 0, 1, 2, 3
        response = await client.get(url, headers=headers)
        if response.status_code != 429:
            return response
        if attempt < 3:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise GitHubServiceError(
        "GitHub API rate limit exceeded. Please try again later.",
        code="GITHUB_FETCH_FAILED",
    )
```

### File Structure — New Files

```
backend/
  app/
    schemas/
      verification.py       ← CREATE (Pydantic request/response models)
    services/
      github_service.py     ← CREATE (all 3 fetch strategies + error class)
    api/
      v1/
        verification.py     ← CREATE (POST /fetch route handler)
        api.py              ← MODIFY (add verification router)
  tests/
    test_verification.py    ← CREATE (Pytest tests)

frontend/
  src/
    lib/
      types/
        verification.ts     ← MODIFY (add FetchedFile, VerificationFetchResponse)
      hooks/
        useVerification.ts  ← CREATE (TanStack mutation hook)
    context/
      SessionContext.tsx    ← MODIFY (add fetchedFiles + setFetchedFiles)
    app/
      session/[sessionId]/
        page.tsx            ← MODIFY (wire onVerify, add handleVerify)
    components/
      pipeline/
        GitHubSourceSelector.tsx  ← MODIFY (placeholder text only)
```

### Previous Story Intelligence (Story 2.1 → 2.2)

From Story 2.1 Dev Agent Record:
- **shadcn/ui Tabs not available** — project uses `@base-ui/react` + native HTML + Tailwind. No shadcn Tabs components.
- **TypeScript strict mode enforced** — `npx tsc --noEmit` must exit 0 before story is considered complete.
- **Vitest is configured** for frontend tests (`npx vitest run <file>`). Pytest for backend.
- **Mobile guard pattern**: `pointer-events-none md:pointer-events-auto` on inner content div (established in `GitHubSourceSelector.tsx`).
- **SessionContext extension pattern**: extend the existing `SessionProvider` in `SessionContext.tsx`; never create a new context.
- **Story 2.1 Review note L2**: `verificationMode`/`githubInput` are read from `SessionContext` — Story 2.2 must read these values from context (not re-pass as props) when calling the fetch API.

### Git Intelligence (Recent Commits)

From latest commit `00e34e2`:
- Files added: `GitHubSourceSelector.tsx`, `GitHubSourceSelector.test.tsx`, `verification.ts` types, `SessionContext.tsx` extended
- No backend verification files exist yet — `github_service.py`, `verification.py`, `schemas/verification.py` are all net-new
- `backend/app/api/v1/api.py` currently only registers `bdd` and `ingestion` routers — needs verification router added

### Error Preservation (Critical AC 4)

When the fetch fails, the frontend MUST NOT clear:
- `bddContent`
- `verificationMode`
- `githubInput`
- `acceptanceCriteria`
- `sessionId`

Only `setGlobalError(message)` should be called. The user should be able to correct their input and retry without losing their BDD work.

### Session Context State Design for Story 2.3

Story 2.3 (LLM Verification) will need the fetched files. Add `fetchedFiles` and `setFetchedFiles` to `SessionContext` now so Story 2.3 can consume them without modifying the context again. Reset `fetchedFiles` to `[]` when a new ticket is ingested (alongside the existing `setVerificationMode(null)` reset in `handleIngestTrigger`).

### Limitations / Out of Scope for This Story

- No LLM call — fetched code is returned to frontend; Story 2.3 wires the LLM
- No `verification_results` DB table write — that is Story 2.3
- No SSE streaming for the fetch endpoint — standard JSON response is sufficient for MVP; the architecture mandates preferring simple request/response unless streaming is genuinely necessary
- No GitHub OAuth — PAT from `settings.github_pat` env var only
- Full repo: if `GITHUB_ACCESS_TOKEN` is empty/missing, raise `GitHubServiceError("GitHub PAT not configured.", code="GITHUB_FETCH_FAILED")` before making any API call

### Implementation Notes

- Route handler returns `JSONResponse` directly (not via `raise HTTPException`) because the app's global `http_exception_handler` reformats the `detail` dict into `{"error": "HTTP_ERROR", "message": <detail>, ...}`, losing the specific error code. Using `JSONResponse` directly ensures the architecture error envelope `{"error": "GITHUB_FETCH_FAILED", "message": "...", "code": 422}` is preserved.
- `asyncio.coroutine` was removed in Python 3.11; tests use proper `async def` side effects with `AsyncMock`.

### References

- Epic 2 story definitions: [epics.md#Story-2.2](../_bmad-output/planning-artifacts/epics.md) lines 460–484
- Architecture — GitHub Verification domain: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 37–38, 248–250, 449
- Architecture — Mandatory rules: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 305–316
- Architecture — Error envelope format: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 172–175
- Architecture — Backend structure: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 244–259
- Architecture — Frontend structure: [architecture.md](../_bmad-output/planning-artifacts/architecture.md) lines 261–270
- Previous story (2.1) dev notes: [2-1-github-source-selection-ui.md](2-1-github-source-selection-ui.md)
- Config (github_pat): [backend/app/core/config.py](../../backend/app/core/config.py) line 46
- Existing hook pattern: [useBDDGenerate.ts](../../frontend/src/lib/hooks/useBDDGenerate.ts)
- LLMProvider ABC pattern (follow same structure for GitHubServiceError): [backend/app/services/llm/provider.py](../../backend/app/services/llm/provider.py)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 4.6

### Debug Log References

- Backend tests: `docker run ... python -m pytest tests/test_verification.py -v` → 33/33 passed
- Full regression: `docker run ... python -m pytest tests/ -v` → 62/62 passed, 0 regressions
- TypeScript: `npx tsc --noEmit` → exit 0

### Completion Notes List

- ✅ Task 1: `backend/app/schemas/verification.py` — `VerificationFetchRequest`, `FetchedFile`, `VerificationFetchResponse` models
- ✅ Task 2: `backend/app/services/github_service.py` — `GitHubServiceError`, all 3 fetch strategies (`fetch_exact_files`, `fetch_full_repo`, `fetch_pull_request`), exponential backoff on 429, public dispatch entrypoint `fetch_github_code`
- ✅ Task 3: `backend/app/api/v1/verification.py` — `POST /fetch` route, `JSONResponse` direct return to preserve error envelope format
- ✅ Task 4: `backend/app/api/v1/api.py` — verification router registered at `/verification`
- ✅ Task 5: `frontend/src/lib/hooks/useVerification.ts` — TanStack `useMutation` hook; `frontend/src/lib/types/verification.ts` extended with `FetchedFile` and `VerificationFetchResponse`
- ✅ Task 6: `frontend/src/app/session/[sessionId]/page.tsx` — `handleVerify` wired to `useVerification()` mutation; error handler preserves all session state; `isVerifying` passed to `GitHubSourceSelector`
- ✅ Task 7: `frontend/src/context/SessionContext.tsx` — `fetchedFiles` + `setFetchedFiles` added; reset on ticket re-ingest
- ✅ Task 8: `frontend/src/components/pipeline/GitHubSourceSelector.tsx` — exact_files placeholder updated to GitHub blob URL format
- ✅ Task 9: `backend/tests/test_verification.py` — 33 tests across URL parsing, all 3 fetch modes, 429 backoff, and API endpoint integration

### File List

- `backend/app/schemas/verification.py` — CREATED
- `backend/app/services/github_service.py` — CREATED
- `backend/app/api/v1/verification.py` — CREATED
- `backend/app/api/v1/api.py` — MODIFIED (verification router added)
- `backend/tests/test_verification.py` — CREATED
- `frontend/src/lib/types/verification.ts` — MODIFIED (FetchedFile, VerificationFetchResponse added)
- `frontend/src/lib/hooks/useVerification.ts` — CREATED
- `frontend/src/context/SessionContext.tsx` — MODIFIED (fetchedFiles + setFetchedFiles)
- `frontend/src/app/session/[sessionId]/page.tsx` — MODIFIED (handleVerify, real isVerifying, removed unused React import)
- `frontend/src/components/pipeline/GitHubSourceSelector.tsx` — MODIFIED (exact_files placeholder text)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — MODIFIED (story status updated)

### Review Follow-ups (AI)

- [ ] [AI-Review][LOW] `_parse_github_blob_url` accepts `http://` URLs (should be `https://` only) [`backend/app/services/github_service.py:96`]
- [ ] [AI-Review][LOW] `fetch_github_code` dispatch has no tests for `full_repo` and `pull_request` modes through the entrypoint function [`backend/tests/test_verification.py:408`]
- [ ] [AI-Review][KNOWN-LIMITATION] Branch names with slashes in blob URLs are not supported — only the first path segment is captured as ref. Document in user-facing help text when this feature is exposed in the UI. [`backend/app/services/github_service.py:96`]
- [ ] [AI-Review][KNOWN-LIMITATION] PR fetch is capped at 100 files per_page (GitHub API max for this endpoint). PRs with >100 changed files will be silently truncated. Pagination loop is out of MVP scope. [`backend/app/services/github_service.py:304`]
- [ ] [AI-Review][STORY-2.3] Filter `fetched_files` entries whose `path` starts with `[WARNING]` before passing to the LLM — these are truncation notices inserted by `fetch_full_repo`. [`backend/app/services/github_service.py:278`]

### Change Log

- 2026-04-05: Story 2.2 implementation — GitHub code fetching service (all 3 modes), verification API endpoint, frontend hook wiring. Status: review.
- 2026-04-05: Code review fixes — H1: added `?per_page=100` to PR files API call; M1: added truncated tree warning to full_repo response; M3: documented branch-with-slash limitation; M4: replaced dual isVerifying state with `verifyMutation.isPending` (single source of truth); M5: verified VALIDATION_ERROR envelope from main.py handler, updated test assertion. Status: done.
