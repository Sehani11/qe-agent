# Story 5.3: Traceability Report Generation API

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want the system to produce a traceability report that links each AC clause to its BDD scenario and verification outcome,
so that I have a single auditable artifact connecting requirements to test results.

## Acceptance Criteria

1. **Given** a session has BDD content and verification results persisted
   **When** a GET request is made to `/api/v1/reports/{session_id}/traceability`
   **Then** the report service returns a structured JSON object linking each AC clause → its BDD scenario → its verification verdict (FR28), assembled **without any LLM call**

2. **And** each row contains: `ac_clause`, `scenario_title`, `scenario_status` (`pass`/`fail`), `justification`, `code_reference`, `github_links`, `implementation_suggestion` (non-null only for failed scenarios), and `rag_context` (FR46, FR45)

3. **And** the report includes the RAG retrieval context used during verification (from each verification result's persisted `rag_context`) (FR45)

4. **And** the traceability data is assembled **only** from the `verification_results` and `bdd_files` tables — no additional LLM/Pinecone call

5. **And** only the authenticated owner of the session can retrieve the report — 403 for a non-owner, 404 for a missing session

6. **And** the report includes session metadata (`session_id`, `jira_ticket_id`, `generated_at`) and a summary (`total`/`passed`/`failed`); a session with no verification results returns a valid report with an empty `rows` list (200, not 404)

## Context & Critical Background

Backend-only, no-LLM data assembly. It joins two existing tables to build the AC → scenario → verdict chain. **The join key is the crux.**

### The traceability join (how AC clauses connect to verdicts)

- **Verdicts** live in `verification_results`, keyed by `scenario_title` (the Gherkin `Scenario:` title parsed during verification) — plus `status`, `justification`, `code_reference`, `github_links`, `implementation_suggestion`, `rag_context`.
- **AC clauses** live in `bdd_files.content`. For `source="generated"`, `content` is a JSON string of `BDDGenerateResponse` → `scenarios: BDDScenario[]`, where each `BDDScenario` has **`source_ac_clause`** and **`scenario`** (the title). `scenariosToGherkin` emits `Scenario: {scenario}`, and verification parses that exact title — so:

  > **Join:** `verification_results.scenario_title` **==** `BDDScenario.scenario` → gives `source_ac_clause`.

- For `source="uploaded"` (raw Gherkin, no structured AC), there is no `source_ac_clause` → `ac_clause` is `null` for those rows (graceful).

**Reuse:**

| Piece | Location |
|---|---|
| `bdd_files` model + "latest row, JSON-parse-if-generated" pattern | `backend/app/models/bdd_file.py`; frontend precedent `session/[sessionId]/page.tsx` (JSON.parse → scenarios) |
| `BDDScenario` (`source_ac_clause`, `scenario`) | `backend/app/schemas/bdd.py` |
| `verification_results` model + `StoredVerificationResult` shape | `backend/app/models/verification_result.py`, `schemas/session.py` |
| Session ownership check (403/404) | `sessions.py` / `chat.py::_verify_session_owner` |
| Router registration | `api/v1/api.py` |
| Scoped-list query pattern | `sessions.py::get_session_verification_results` |

## Tasks / Subtasks

### Backend — schemas (AC2, AC6)

- [x] **Task 1: `schemas/report.py`** (AC: 2, 6)
  - [x] `class TraceabilityRow(BaseModel)`: `ac_clause: str | None`, `scenario_title: str`, `scenario_status: str`, `justification: str`, `code_reference: dict`, `github_links: list`, `implementation_suggestion: str | None`, `rag_context: list[dict] | None`
  - [x] `class TraceabilitySummary(BaseModel)`: `total: int`, `passed: int`, `failed: int`
  - [x] `class TraceabilityReport(BaseModel)`: `session_id: str`, `jira_ticket_id: str`, `generated_at: datetime`, `summary: TraceabilitySummary`, `rows: list[TraceabilityRow]`

### Backend — service (AC1–4, AC6)

- [x] **Task 2: `services/report_service.py::build_traceability_report(session_id, db) -> TraceabilityReport`** (AC: 1, 2, 3, 4, 6)
  - [x] Load the latest `bdd_files` row for the session (`ORDER BY created_at DESC LIMIT 1`)
  - [x] Build an `{scenario_title: source_ac_clause}` map: if `bdd_file.source == "generated"`, `json.loads(content)["scenarios"]` → map `s["scenario"] → s["source_ac_clause"]`; if `uploaded` (or JSON malformed), map is empty
  - [x] Load all `verification_results` for the session (`ORDER BY created_at ASC`)
  - [x] For each result → `TraceabilityRow(ac_clause=map.get(scenario_title), scenario_title=..., scenario_status=status, justification=..., code_reference=..., github_links=..., implementation_suggestion=..., rag_context=...)`
  - [x] Compute summary (total/passed/failed); `generated_at = datetime.now(UTC)`
  - [x] **No LLM, no Pinecone** — pure DB read + assembly (AC4)
  - [x] Load the `Session` row for `jira_ticket_id` (and pass ownership info back / or let the route own the check)

### Backend — endpoint + routing (AC1, AC5)

- [x] **Task 3: `GET /api/v1/reports/{session_id}/traceability`** (AC: 1, 5, 6)
  - [x] New `api/v1/reports.py`: verify the session exists and `session.user_id == current_user` → 404/403 (mirror `chat.py::_verify_session_owner`) **before** assembling
  - [x] Return `await report_service.build_traceability_report(session_id, db)` as `TraceabilityReport`
  - [x] Register in `api/v1/api.py`: `api_router.include_router(reports.router, prefix="/reports", tags=["reports"])`

### Tests (AC1–6)

- [x] **Task 4: `backend/tests/test_reports.py`**
  - [x] Happy path: generated BDD (2 scenarios) + 2 verification results → rows carry the correct `ac_clause` matched by title, correct status/justification/rag_context, and a `total/passed/failed` summary
  - [x] Uploaded BDD (raw Gherkin) → rows have `ac_clause = null` (no crash)
  - [x] Malformed/absent BDD JSON → empty AC map, rows still returned with `ac_clause = null`
  - [x] No verification results → 200 with `rows = []` and a zeroed summary
  - [x] Ownership: 403 non-owner, 404 missing session
  - [x] **No-LLM guarantee:** assert no LLM/Pinecone is invoked (mock DB only; nothing else needed)
  - [x] Mirror `test_sessions.py` mocking (override `get_current_user` + `get_db`, MagicMock rows — set every schema-read attr per the 4.4 MagicMock caveat)

## Dev Notes

### The join, concretely

`scenariosToGherkin` ([useBDDGenerate.ts:25](frontend/src/lib/hooks/useBDDGenerate.ts#L25)) writes `Scenario: {scenario.scenario}` (with a `# Source AC:` comment above it). Verification's `parse_bdd_scenarios` captures the `Scenario:` title into `verification_results.scenario_title`. So a verdict's `scenario_title` equals the BDD scenario's `scenario` field — that's the map key to recover `source_ac_clause`. Title collisions (two scenarios with the same title) are unlikely; if they occur, last-wins in the map is acceptable for MVP (note in the report if surfaced).

### bdd_files content format (mirror the frontend)

Identical to the format the session page already parses ([session/[sessionId]/page.tsx](frontend/src/app/session/[sessionId]/page.tsx)):
- `source == "generated"` → `content` is `JSON.stringify(BDDGenerateResponse)` → `.scenarios[]`
- `source == "uploaded"` → `content` is raw Gherkin (no `source_ac_clause`)

Wrap the `json.loads` in try/except → empty map on failure (AC "malformed BDD" case).

### rag_context passthrough (AC3/FR45)

Each `verification_results` row already persists `rag_context` (JSONB, added in Story 4.3/4.4). Pass it straight into the `TraceabilityRow` — no transformation. This is how RAG context reaches the report (and later the PDF/CSV in Story 5.4).

### Scope / ordering

- Route stays logic-free beyond the ownership check; assembly lives in `report_service`.
- Rows ordered by verdict `created_at ASC` (stable, matches verification order).
- This is the **data source for Story 5.4** (PDF/CSV export) — keep the shape export-friendly (flat rows).

### Project Structure Notes

**Backend — modify:** `api/v1/api.py` (register reports router)
**Backend — create:** `schemas/report.py`, `services/report_service.py`, `api/v1/reports.py`, `tests/test_reports.py`
**Do NOT modify:** `verification_results`/`bdd_files` models or the verification/BDD services; no migration (reads only)

### Testing Standards

- `pytest`/`pytest-asyncio`; override `get_current_user` + `get_db`; `MagicMock` rows with all schema-read attrs set (4.4 caveat). Build the generated-BDD `content` as a real `json.dumps({"scenarios": [...]})` string in-test. Full-suite regression + ruff-clean on new code.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 5, Story 5.3 (FR28, FR45, FR46)
- [Source: backend/app/schemas/bdd.py:17-27] — `BDDScenario` (`source_ac_clause`, `scenario`)
- [Source: backend/app/models/bdd_file.py] — `bdd_files` (content + source)
- [Source: backend/app/models/verification_result.py] — verdict fields incl. `rag_context`
- [Source: backend/app/schemas/session.py:34-51] — `StoredVerificationResult` shape (row fields)
- [Source: backend/app/api/v1/sessions.py:138-168] — scoped verification-results query pattern
- [Source: backend/app/api/v1/chat.py] — `_verify_session_owner` (403/404) to mirror
- [Source: backend/app/api/v1/api.py] — router registration
- [Source: frontend/src/lib/hooks/useBDDGenerate.ts:25-45] — `scenariosToGherkin` (proves the title join)
- [Source: frontend/src/app/session/[sessionId]/page.tsx] — generated-BDD JSON parse precedent
- [Source: _bmad-output/implementation-artifacts/3-5-session-history-dashboard.md] — bdd_files.content format table + session-scoped query patterns

### Open Questions (non-blocking)

- **Q1 — Uploaded BDD AC clauses:** for `source="uploaded"` there's no `source_ac_clause` (rows get `ac_clause=null`). Optionally parse the `# Source AC:` Gherkin comment if present — defer unless needed.
- **Q2 — Orphan verdicts:** a verdict whose `scenario_title` isn't in the BDD map (e.g. BDD edited after verification) gets `ac_clause=null`. Acceptable, or flag such rows? Defaulting to `null`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New code ruff-clean (one RUF010 fix: `{str(uuid.uuid4())}` → `{uuid.uuid4()!s}` in a test f-string).
- Minor, deliberate signature deviation from the story: `build_traceability_report(session, db)` takes the already-loaded (ownership-verified) `Session` ORM object instead of `(session_id, db)` — avoids a redundant session query and provides `jira_ticket_id` directly. The route owns the ownership check + session load.

### Completion Notes List

- **The join (AC1/AC2):** `_build_ac_map` turns the latest **generated** `bdd_files.content` (`json.loads(...)["scenarios"]`) into `{scenario → source_ac_clause}`; each `verification_results` row is matched by `scenario_title` to recover its `ac_clause`. No LLM/Pinecone anywhere (AC4).
- **Graceful edges (AC6, Q1/Q2):** uploaded BDD (`source != "generated"`) → empty map → `ac_clause=null`; malformed JSON caught (`JSONDecodeError`/`TypeError`/`AttributeError`) → empty map, rows still returned; a verdict whose title isn't in the map → `null`. No verification results → 200 with `rows=[]` and a zeroed summary.
- **rag_context (AC3/FR45):** passed straight from each verdict into the `TraceabilityRow` — the report's RAG context.
- **Ownership (AC5):** route loads the `Session`, 404 if missing, 403 if `user_id != current_user`, before assembly.
- **Export-friendly:** flat `TraceabilityRow` shape, ordered by verdict `created_at ASC` — the data source for Story 5.4 (PDF/CSV).
- **Validation:** backend **230 pass** (+6 report tests), new code ruff-clean.

### File List

**Backend — modified:**
- `backend/app/api/v1/api.py` — registered the reports router

**Backend — created:**
- `backend/app/schemas/report.py` — `TraceabilityRow`, `TraceabilitySummary`, `TraceabilityReport`
- `backend/app/services/report_service.py` — `build_traceability_report` + `_build_ac_map`
- `backend/app/api/v1/reports.py` — `GET /reports/{session_id}/traceability` (ownership-checked)
- `backend/tests/test_reports.py` — 6 tests (generated/uploaded/malformed BDD, empty report, 403/404)

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-05 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 6 ACs implemented; ownership (403/404) and the no-LLM guarantee hold.

### Action Items — all resolved

- [x] **[M1][Med]** The AC-map join was silently fragile: verification stores `scenario_title` **stripped** (`parse_bdd_scenarios` `.strip()`), but `_build_ac_map` keyed on the raw `s["scenario"]` → any whitespace difference dropped the `ac_clause` to null. Fixed: key the map on `title.strip()` (and guard `isinstance(title, str)`). Added `test_traceability_matches_ac_despite_whitespace_in_title` (padded BDD title still joins). `[report_service.py]`
- [x] **[L1][Low]** "Latest BDD" may not be the BDD verification ran against (uploaded-after-generate) → added a code comment documenting the heuristic/limitation. `[report_service.py]`
- [x] **[L2][Low]** No test for an orphan verdict (title absent from a populated map) → added `test_traceability_orphan_verdict_gets_null_ac`.

**Post-fix validation:** backend **232 pass** (+2), new code ruff-clean.

## Change Log

- 2026-07-05: Implemented Story 5.3 — Traceability Report Generation API. Added `GET /api/v1/reports/{session_id}/traceability` assembling AC → BDD scenario → verdict rows from `bdd_files` + `verification_results` (no LLM), joined by `scenario_title == BDDScenario.scenario`, with `rag_context` passthrough (FR45) and graceful null AC clauses for uploaded/malformed BDD. Backend 230 tests pass (+6).
- 2026-07-05: Code review (adversarial) — Approve. Resolved 1 Medium + 2 Low: stripped the AC-map key so the join survives whitespace (M1), documented the latest-BDD limitation (L1), added an orphan-verdict test (L2). Backend 232 pass.
