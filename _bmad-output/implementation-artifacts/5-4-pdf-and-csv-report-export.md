# Story 5.4: PDF & CSV Report Export

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to download my traceability report as a PDF or CSV file,
so that I can share verification evidence with stakeholders and attach it to PRs or Jira tickets.

## Acceptance Criteria

1. **Given** verification results and traceability data exist for a session
   **When** the user clicks "Download PDF"
   **Then** the FastAPI report service generates a formatted PDF from the traceability report and the browser downloads it as `{jira_ticket_id}_report.pdf` (FR29)

2. **And** the PDF includes the RAG retrieval context and implementation suggestions alongside each scenario's verdict (FR45, FR46)

3. **And** the generated PDF is stored in the `reports` Supabase Storage bucket via `StorageService` (FR42) — and a storage failure/unavailability does **not** break the download (the bytes are still returned to the user; the storage error is logged, not surfaced fatally)

4. **And given** the user clicks "Download CSV"
   **When** the CSV is generated from the traceability data
   **Then** the file downloads as `{jira_ticket_id}_report.csv` with columns: **AC Clause, Scenario, Status, Justification, Code Reference, Implementation Suggestion, RAG Context** (FR30, FR45, FR46)

5. **And** both exports are triggered from the frontend via `useVerification()` (TanStack Query) mutation hooks, reusing the established blob-download pattern

6. **And** the export buttons render in the verification results panel and are **only enabled when verification results exist** for the session (i.e. at least one row)

7. **And** **no LLM and no Pinecone call** happens during export — the data is assembled entirely from `verification_results` + `bdd_files` by reusing Story 5.3's `report_service.build_traceability_report(...)`

8. **And** only the authenticated owner of the session can export — **404** for a missing session, **403** for a non-owner (mirror the existing traceability endpoint); a session with **zero** verification results still produces a valid (header-only CSV / empty-table PDF) export, not an error

## Context & Critical Background

This is the **final story of the Reports track** (5.3 → 5.4) and it is **pure rendering + delivery**. Story 5.3 already built the entire data-assembly engine: [`report_service.build_traceability_report(session, db)`](backend/app/services/report_service.py#L57) returns a `TraceabilityReport` with flat, export-friendly `rows`. **Do not re-assemble, re-query verification data differently, or add any join logic here** — call that function and render its result to bytes.

### What 5.4 adds (the whole job)

1. Two render functions in `report_service.py`: `TraceabilityReport → PDF bytes` and `TraceabilityReport → CSV bytes`.
2. Two GET routes in the existing `api/v1/reports.py` that: verify ownership (already patterned there) → call `build_traceability_report` → render → (best-effort) persist the PDF to the `reports` bucket → return a file download response.
3. Frontend: two mutation hooks in `useVerification.ts` that fetch the bytes as a blob and trigger a browser download, plus two buttons in `VerificationResultsPanel.tsx`.

### 🚨 CRITICAL DECISION — PDF library is NOT installed

The backend has **no PDF-generation library**. [`backend/pyproject.toml`](backend/pyproject.toml#L22) only ships `pypdf` (a **reader/merger**, not a generator). You must add one via `uv add` in `backend/`.

**Recommended: `reportlab`** — pure-Python, zero system/OS dependencies (Docker-safe), the de-facto standard for programmatic PDF generation. Use its `SimpleDocTemplate` + `Table`/`Paragraph` flowables.

- ❌ **Do NOT use WeasyPrint** — it needs native system libraries (Pango/Cairo/GDK-Pixbuf) that are **not** in the backend Docker image; it will fail at import/render time in the container. (See [architecture.md](_bmad-output/planning-artifacts/architecture.md) — NFR-R5: `docker compose up` must work out of the box.)
- Acceptable lighter alternative: `fpdf2` (also pure-Python) if you prefer a smaller dependency, but `reportlab` handles multi-line table cells / wrapping far more gracefully for justifications and RAG snippets.

After `uv add reportlab`, confirm `pyproject.toml` **and** `uv.lock` are updated and the image still builds.

### Reuse map (do not reinvent)

| Piece | Location | Use for |
|---|---|---|
| `build_traceability_report(session, db)` → `TraceabilityReport` | [report_service.py:57](backend/app/services/report_service.py#L57) | **The** data source — call it, render its output |
| `TraceabilityReport` / `TraceabilityRow` shapes | [schemas/report.py](backend/app/schemas/report.py) | Field names to render (see field notes below) |
| Ownership check (404 missing / 403 non-owner) | [reports.py:28-33](backend/app/api/v1/reports.py#L28) | Copy verbatim into the two new routes (or extract a shared helper) |
| `StorageService.upload_file(folder, path, bytes, content_type, user_id)` + `FOLDER_REPORTS` | [storage_service.py:92](backend/app/services/storage_service.py#L92) | FR42 PDF persistence; check `storage_service.is_available` first |
| Router already registered | [api.py:24](backend/app/api/v1/api.py#L24) (`/reports`) | New routes just attach to the same `router` |
| CSV escaping + `downloadBlob` browser pattern | [BDDEditorPanel.tsx:73-99](frontend/src/components/pipeline/BDDEditorPanel.tsx#L73) | Frontend download mechanics (reuse the exact `downloadBlob` shape) |
| Export/download trigger lives in `useVerification.ts` | [architecture.md:453](_bmad-output/planning-artifacts/architecture.md#L453) | Where the two new hooks belong |
| Session ID + `verificationResults` in context | [SessionContext.tsx:22,50](frontend/src/context/SessionContext.tsx#L22) | Button enable/disable + which session to export |
| authed axios client (Bearer auto-attached) | [client.ts](frontend/src/lib/api/client.ts) | Use for blob requests (`responseType: "blob"`) |

### `TraceabilityRow` field → export mapping

`TraceabilityRow` ([schemas/report.py:12](backend/app/schemas/report.py#L12)) fields and how each renders:

| Row field | Type | CSV column | Rendering note |
|---|---|---|---|
| `ac_clause` | `str \| None` | AC Clause | `None` → empty string (uploaded/orphan BDD) |
| `scenario_title` | `str` | Scenario | as-is |
| `scenario_status` | `str` (`pass`/`fail`) | Status | as-is |
| `justification` | `str` | Justification | multi-line — wrap in PDF; escape in CSV |
| `code_reference` | `dict` | Code Reference | **serialize** (e.g. `file:function:line` or compact JSON) — it is a dict, not a string |
| `implementation_suggestion` | `str \| None` | Implementation Suggestion | `None` (pass rows) → empty string |
| `rag_context` | `list[dict] \| None` | RAG Context | **serialize** to readable text (e.g. join each source's title/snippet with `; `); `None` → empty |

The report envelope also carries `session_id`, `jira_ticket_id`, `generated_at`, and `summary {total, passed, failed}` — surface `jira_ticket_id`, `generated_at`, and the summary in the **PDF header** for a professional artifact.

## Tasks / Subtasks

### Backend — dependency (AC 1)

- [x] **Task 1: Add the PDF library** (AC: 1)
  - [x] In `backend/`, run `uv add reportlab` (or `fpdf2` if chosen) — commit the updated `pyproject.toml` + `uv.lock`
  - [x] Verify the backend image still builds (`docker compose build backend` or local `uv sync`)

### Backend — rendering (AC 1, 2, 4, 7)

- [x] **Task 2: `report_service.render_report_csv(report: TraceabilityReport) -> bytes`** (AC: 4, 7)
  - [x] Header row exactly: `AC Clause, Scenario, Status, Justification, Code Reference, Implementation Suggestion, RAG Context`
  - [x] One data row per `report.rows` entry; `None`/dict/list fields serialized per the mapping table above
  - [x] Use the stdlib `csv` module (`io.StringIO` → `.encode("utf-8")`) — quotes/newlines handled by `csv.writer`; do **not** hand-roll escaping
  - [x] Zero rows → header-only CSV (still valid, AC 8)
- [x] **Task 3: `report_service.render_report_pdf(report: TraceabilityReport) -> bytes`** (AC: 1, 2, 7)
  - [x] Header block: title, `jira_ticket_id`, `generated_at`, summary (`total / passed / failed`)
  - [x] One section/table row per scenario showing: AC clause, scenario title, status, justification, code reference, **implementation suggestion (fail rows)** and **RAG context** (AC 2 = FR45/FR46)
  - [x] Render to an in-memory buffer (`io.BytesIO`) and return `.getvalue()` — no temp files on disk
  - [x] Zero rows → valid PDF with header + "No verification results" note (AC 8)
  - [x] **No LLM, no Pinecone** anywhere in either renderer (AC 7)

### Backend — endpoints + storage (AC 1, 3, 5, 8)

- [x] **Task 4: `GET /api/v1/reports/{session_id}/export/pdf`** (AC: 1, 3, 8)
  - [x] In existing `api/v1/reports.py`: load `Session`, 404 if missing, 403 if `user_id != current_user` (reuse/extract the check already in `get_traceability_report`)
  - [x] `report = await report_service.build_traceability_report(session, db)` → `pdf_bytes = report_service.render_report_pdf(report)`
  - [x] **Best-effort persist (FR42):** if `storage_service.is_available`, `await storage_service.upload_file(FOLDER_REPORTS, f"{session_id}/{report.jira_ticket_id}_report.pdf", pdf_bytes, "application/pdf", user_id=current_user)`; wrap in try/except → log `StorageServiceError`, **do not fail the request** (AC 3)
  - [x] Return a FastAPI `Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{report.jira_ticket_id}_report.pdf"'})`
- [x] **Task 5: `GET /api/v1/reports/{session_id}/export/csv`** (AC: 4, 8)
  - [x] Same ownership check → `build_traceability_report` → `render_report_csv`
  - [x] Return `Response(content=csv_bytes, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{report.jira_ticket_id}_report.csv"'})`
  - [x] (CSV storage is optional per scope — PDF persistence is the FR42 requirement; only add CSV upload if trivially symmetric)

### Frontend — hooks + buttons (AC 5, 6)

- [x] **Task 6: Export mutation hooks in `useVerification.ts`** (AC: 5)
  - [x] `useExportReportPdf()` / `useExportReportCsv()` — `useMutation` calling `apiClient.get(\`/reports/${sessionId}/export/pdf\`, { responseType: "blob" })`, then trigger a browser download of the returned blob
  - [x] Reuse the `downloadBlob(blob-or-content, filename, mimeType)` pattern from [BDDEditorPanel.tsx:73](frontend/src/components/pipeline/BDDEditorPanel.tsx#L73) (extract to a shared util if cleaner); filename `{jira_ticket_id}_report.pdf` / `.csv` — read the Content-Disposition filename if present, else fall back to `sessionId`
- [x] **Task 7: Export buttons in `VerificationResultsPanel.tsx`** (AC: 6)
  - [x] "Download PDF" + "Download CSV" buttons in the results panel header area
  - [x] `disabled` unless `verificationResults.length > 0`; show a pending/spinner state while the mutation is in flight; surface an inline error on failure (no silent failure, NFR-R1)

### Tests (AC 1–8)

- [x] **Task 8: Backend `backend/tests/test_reports.py` (extend)**
  - [x] PDF export: 200, `Content-Type: application/pdf`, body starts with `%PDF-`, `Content-Disposition` has `{jira_ticket_id}_report.pdf`
  - [x] CSV export: 200, `text/csv`, first line is the exact header, one data row per verification result, `None` fields render empty, dict/list fields serialized (not `"{...}"` Python-repr garbage that breaks columns)
  - [x] Ownership: 403 non-owner, 404 missing session (both endpoints)
  - [x] Empty report (no verification results): CSV = header-only 200; PDF = valid 200 (AC 8)
  - [x] **Storage best-effort (AC 3):** mock `storage_service.upload_file` — assert it's called for PDF; and assert that when it **raises** `StorageServiceError`, the endpoint still returns 200 with the bytes
  - [x] **No-LLM/Pinecone guarantee:** mock DB only; assert no LLM/Pinecone client is constructed or called
  - [x] Mirror the existing `test_reports.py` mocking (override `get_current_user` + `get_db`, `MagicMock` rows with every schema-read attr set — the 4.4 MagicMock caveat)

## Dev Notes

### Scope discipline (read this first)

5.4 is **rendering + delivery only**. The AC→BDD→verdict join, `rag_context` passthrough, and null-handling are **already done and tested** in 5.3. If you find yourself querying `verification_results` or `bdd_files` directly in this story, stop — call `build_traceability_report(session, db)` instead. The one signature note from 5.3: `build_traceability_report` takes the **already-loaded, ownership-verified `Session` ORM object** (not `session_id`), so the route must load `Session` first (which it already does for the ownership check).

### Serializing non-string row fields (the CSV footgun)

`code_reference` is a `dict` and `rag_context` is a `list[dict]`. Writing them raw into CSV via `str()` yields Python-repr (`{'file': ...}`) which is ugly and can contain commas/quotes that `csv.writer` will quote as one cell (correct) but is unreadable. Serialize deliberately:
- `code_reference` → e.g. `f"{cr.get('file','')}:{cr.get('function','')}:{cr.get('line','')}"` (adapt to the actual dict shape — inspect a real `verification_results.code_reference` value; the verdict shape is defined by Epic 2 Story 2.3).
- `rag_context` → join each source dict into `"{title}: {snippet}"` segments with `" | "`; `None` → `""`.
Keep the same serialization in the PDF so the two exports agree.

### Storage path & FR42

Persist under the user namespace the way `StorageService` already enforces: pass `user_id=current_user` and `folder=FOLDER_REPORTS`; the service prepends `{user_id}/reports/...`. Use path `f"{session_id}/{jira_ticket_id}_report.pdf"` so multiple sessions don't collide. Persistence is **best-effort** (AC 3): storage being unconfigured in local/dev (`is_available == False`) or a Supabase hiccup must not block the user's download.

### Frontend download mechanics

`apiClient` ([client.ts](frontend/src/lib/api/client.ts)) auto-attaches the Bearer token, so blob GETs are already authed. Set `responseType: "blob"`. The existing `downloadBlob` in `BDDEditorPanel` builds a `Blob` from a string; for the report you already have a `Blob` from axios — create the object URL directly from it. Buttons live in `VerificationResultsPanel` which already reads `verificationResults` and `sessionId` from `useSessionContext()`.

### Endpoint shape consistency

The existing traceability route is `GET /reports/{session_id}/traceability`. Keep the new ones parallel: `GET /reports/{session_id}/export/pdf` and `GET /reports/{session_id}/export/csv`. GET (not POST) is fine — export is idempotent and side-effect-light (the storage write is a cache, not user-visible state).

### Previous Story Intelligence (5.3)

From [5-3-traceability-report-generation-api.md](_bmad-output/implementation-artifacts/5-3-traceability-report-generation-api.md):
- The report rows are already ordered by verdict `created_at ASC` — preserve that order in both exports (don't re-sort).
- 5.3's review (M1) fixed a whitespace-sensitivity in the AC join — you inherit the corrected `ac_clause` values for free; no need to touch the join.
- 5.3 test conventions: build generated-BDD `content` as a real `json.dumps({"scenarios":[...]})` string in-test; override `get_current_user` + `get_db`; set **every** schema-read attribute on `MagicMock` rows (a missing attr surfaces as a `MagicMock` object and corrupts rendering).
- Backend suite was at **232 pass** after 5.3 — run the full suite; new code must be ruff-clean (`ruff check` selects `E,F,I,N,W,UP,B,SIM,RUF`, line-length 88).

### Project Structure Notes

**Backend — modify:** `pyproject.toml` + `uv.lock` (add reportlab); `app/api/v1/reports.py` (2 new routes); `app/services/report_service.py` (2 render functions); `backend/tests/test_reports.py` (extend).
**Frontend — modify:** `src/lib/hooks/useVerification.ts` (2 hooks); `src/components/pipeline/VerificationResultsPanel.tsx` (2 buttons).
**Do NOT modify:** `schemas/report.py`, the 5.3 `build_traceability_report`/`_build_ac_map` logic, `verification_results`/`bdd_files` models, `StorageService`. No DB migration (reads only).
**Alignment:** matches architecture ([architecture.md:395](_bmad-output/planning-artifacts/architecture.md#L395) `reports.py ← PDF/CSV export routes`; [:453](_bmad-output/planning-artifacts/architecture.md#L453) `useVerification.ts (download trigger)`). No variances.

### Testing Standards

`pytest`/`pytest-asyncio` (`asyncio_mode=auto`), `httpx` test client; override `get_current_user` + `get_db`; `MagicMock` rows with all schema-read attrs set. Assert the PDF magic header (`%PDF-`) rather than pixel content. Full-suite regression + ruff-clean on all new backend code. Frontend: if the repo has component tests (see `__tests__/VerificationResultsPanel.test.tsx`), add button enable/disable + mutation-trigger coverage there.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.4] — Story 5.4 ACs (FR29, FR30, FR42, FR45, FR46)
- [Source: backend/app/services/report_service.py:57] — `build_traceability_report` (the data source; takes a `Session` object)
- [Source: backend/app/schemas/report.py:12-43] — `TraceabilityRow` / `TraceabilityReport` field shapes to render
- [Source: backend/app/api/v1/reports.py:16-35] — existing traceability route + ownership check to mirror
- [Source: backend/app/services/storage_service.py:92] — `upload_file` signature + `FOLDER_REPORTS`; `is_available` guard
- [Source: backend/app/api/v1/api.py:24] — `/reports` router already registered
- [Source: backend/pyproject.toml:22] — proves only `pypdf` (reader) is present; PDF generator must be added
- [Source: frontend/src/components/pipeline/BDDEditorPanel.tsx:73-99] — `downloadBlob` + CSV precedent to reuse
- [Source: frontend/src/lib/hooks/useVerification.ts] — where export mutation hooks belong (per architecture)
- [Source: frontend/src/components/pipeline/VerificationResultsPanel.tsx] — where export buttons render; reads `verificationResults`/`sessionId`
- [Source: frontend/src/lib/api/client.ts] — authed axios client (use `responseType: "blob"`)
- [Source: _bmad-output/planning-artifacts/architecture.md:395,453] — reports.py = PDF/CSV routes; useVerification.ts = download trigger
- [Source: _bmad-output/implementation-artifacts/5-3-traceability-report-generation-api.md] — upstream data engine + test conventions

### Open Questions (non-blocking)

- **Q1 — PDF library choice:** `reportlab` (recommended, richer tables) vs `fpdf2` (lighter). Either satisfies the ACs; picked reportlab in the notes. Confirm during Task 1 and record in File List.
- **Q2 — CSV storage:** FR42 explicitly requires the *PDF* in the bucket; CSV persistence is not mandated. Defaulting to PDF-only persistence; add CSV upload only if symmetric and cheap.
- **Q3 — `code_reference` dict shape:** inspect a real persisted `verification_results.code_reference` (Epic 2 Story 2.3 defines it) to pick the exact serialization keys; the notes assume `file`/`function`/`line`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv add reportlab` → installed `reportlab==5.0.0` (+ `pillow==12.3.0` transitive); `pyproject.toml` + `uv.lock` updated. Pure-Python, Docker-safe (WeasyPrint deliberately avoided per NFR-R5).
- Two ruff E501 line-length fixes on new code (log message + PDF `data.append` call); new backend code ruff-clean.
- Frontend `tsc` surfaces a **pre-existing** error in `src/lib/hooks/useKnowledge.ts:129` (untouched by this story). Confirmed unrelated.
- Full frontend suite has 10 **pre-existing** failures in `auth.test.ts`, `GitHubSourceSelector.test.tsx`, `BDDEditorPanel.test.tsx` — reproduced identically with this story's changes stashed (baseline). Not introduced here.

### Completion Notes List

- **Pure render + delivery (AC7):** both endpoints reuse Story 5.3's `report_service.build_traceability_report(session, db)` — no new joins, no LLM, no Pinecone. Added `render_report_csv` / `render_report_pdf` (report → bytes) plus `_fmt_code_reference` / `_fmt_rag_context` serializers.
- **CSV (AC4):** stdlib `csv.writer` → exact header `AC Clause, Scenario, Status, Justification, Code Reference, Implementation Suggestion, RAG Context`. `code_reference` dict → `file:function:line`; `rag_context` list → `"{source}: {title} — {snippet}"` joined by ` | ` (readable, never Python-repr); `None` → empty cell.
- **PDF (AC1/AC2):** reportlab `SimpleDocTemplate` — header (ticket, generated-at, summary) + one bordered table per scenario carrying status, AC clause, justification, code reference, and (when present) implementation suggestion (FR46) + RAG context (FR45). Rendered fully in-memory (`io.BytesIO`).
- **Storage best-effort (AC3/FR42):** `_persist_report` uploads the PDF to `FOLDER_REPORTS` via `StorageService` scoped to `user_id`; guarded by `is_available` and a `StorageServiceError` try/except so a storage outage never breaks the download. Verified by `test_export_pdf_survives_storage_error`.
- **Ownership (AC8):** extracted `_load_owned_session` (404 missing / 403 non-owner), reused by all three report routes.
- **Frontend (AC5/AC6):** `useExportReportPdf` / `useExportReportCsv` mutation hooks in `useVerification.ts` fetch `responseType: "blob"` (authed axios) and trigger a browser download, reading the `Content-Disposition` filename. Buttons added to `VerificationResultsPanel`, `disabled` unless `verificationResults.length > 0 && sessionId`, with pending + inline-error states.
- **Filenames:** `{jira_ticket_id}_report.pdf` / `{jira_ticket_id}_report.csv` (AC1/AC4).
- **Validation:** backend **242 pass** (+10 report export tests); new backend code ruff-clean. Frontend `VerificationResultsPanel` suite **13 pass / 1 skipped** (+2 new export-button tests); changed frontend files tsc- and eslint-clean.
- **Open Questions resolved:** Q1 → chose `reportlab`. Q2 → PDF-only persistence (FR42 mandates PDF; CSV not persisted). Q3 → `code_reference` serialized as `file:function:line` matching the `CodeReference` schema.

### File List

**Backend — modified:**
- `backend/pyproject.toml` — added `reportlab>=5.0.0`
- `backend/uv.lock` — locked `reportlab` + `pillow`
- `backend/app/services/report_service.py` — `render_report_csv`, `render_report_pdf`, `_fmt_code_reference`, `_fmt_rag_context`, `CSV_COLUMNS`
- `backend/app/api/v1/reports.py` — `GET /export/pdf`, `GET /export/csv`, `_load_owned_session`, `_persist_report` (best-effort storage)
- `backend/tests/test_reports.py` — 10 new export tests (PDF/CSV bytes, storage persist + failure resilience, serialization, empty report, 403/404)

**Frontend — modified:**
- `frontend/src/lib/hooks/useVerification.ts` — `useExportReportPdf`, `useExportReportCsv` + blob-download helpers
- `frontend/src/components/pipeline/VerificationResultsPanel.tsx` — Download PDF / CSV buttons (enable-gated, pending + error states)
- `frontend/src/components/pipeline/__tests__/VerificationResultsPanel.test.tsx` — mocked export hooks + 2 button tests

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-05 · **Outcome:** Approve (all High/Medium/Low findings fixed)

Git File List matched actual changes (uv.lock is gitignored — noted, not committable). All 8 ACs implemented; ownership (403/404), no-LLM guarantee, and best-effort storage hold. Two genuine defects were found by empirically driving the PDF renderer with realistic markup-bearing content.

### Action Items — all resolved

- [x] **[H1][High]** PDF export passed unescaped LLM/user text into reportlab `Paragraph()` (parses text as mini-XML). **Proven** twofold: (a) unbalanced markup (`unbalanced <b>bold`) raised `ValueError: Parse error` → **500 on export**; (b) `foo<T>(x)` was silently rendered as `foo(x)` (angle-bracket spans swallowed → corrupted audit evidence). Fixed: `xml.sax.saxutils.escape` applied to every dynamic value (ticket id, scenario title, status, AC clause, justification, code reference, suggestion, RAG context) before embedding in `Paragraph`. Added `test_export_pdf_survives_markup_content` (builds + `pypdf`-extracts, asserts `foo<T>(x)` survives and no crash). `[report_service.py::render_report_pdf]`
- [x] **[M1][Med]** CSV formula injection (CWE-1236): cells beginning with `= + - @` (or a control-char lead-in) execute as formulas in Excel/Sheets, and the report is shared with stakeholders. Fixed: `_sanitize_csv_cell` prefixes such cells with `'`. Added `test_export_csv_neutralizes_formula_injection`. `[report_service.py::render_report_csv]`
- [x] **[M2][Med]** Test-quality gap that let H1 through — PDF tests only asserted the `%PDF-` magic bytes, never rendered content. Fixed by the content-extraction test above.
- [x] **[L1][Low]** `Content-Disposition` filename built from raw `jira_ticket_id` (quote/control-char injection). Fixed: `_safe_ticket_slug` whitelists `[A-Za-z0-9._-]`. Added `test_export_filename_sanitized_for_unsafe_ticket_id`. `[reports.py]`
- [ ] **[L2][Low]** `uv.lock` is gitignored repo-wide, so the reportlab pin isn't committed (pyproject `reportlab>=5.0.0` still installs it). No action — repo-wide convention, out of story scope.

**Post-fix validation:** backend **245 pass** (+3 review-fix tests), new code ruff-clean. Frontend untouched by fixes.

## Change Log

- 2026-07-05: Implemented Story 5.4 — PDF & CSV Report Export. Added `GET /api/v1/reports/{session_id}/export/{pdf,csv}` rendering Story 5.3's traceability report to bytes (reportlab PDF / stdlib CSV, no LLM), with RAG context + implementation suggestions (FR45/FR46), best-effort PDF persistence to the `reports` bucket (FR42), and ownership 403/404. Frontend export hooks + buttons in the verification results panel. Backend 242 tests pass (+10); new code ruff-clean.
- 2026-07-05: Code review (adversarial) — Approve. Resolved 1 High + 2 Medium + 1 Low: escaped all PDF content to fix a 500-crash + silent data-loss on markup-bearing text (H1), neutralized CSV formula injection (M1), added PDF content-correctness test (M2), sanitized the export filename (L1). Backend 245 pass.
