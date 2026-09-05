# Story 4.6: Document Upload Knowledge Ingestion (PDF / DOCX)

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): Uploaded documents are embedded into the active project's namespace; the upload carries `project_id` as a form field.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): Document source ids are now filename-keyed (`doc-{sha256(filename)[:16]}`), so re-uploading a document REPLACES the previous version (vectors pre-cleaned, `knowledge_sources` row updated in place) instead of duplicating its chunks in retrieval. Documents ingested before this change keep their uuid ids until deleted once manually.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to upload PDF and DOCX documents into my knowledge base (alongside Confluence and Jira),
so that project docs that don't live in Confluence/Jira can still enrich RAG verification.

## Acceptance Criteria

1. **Given** an authenticated user uploads a `.pdf` or `.docx` file
   **When** a multipart POST is made to `/api/v1/knowledge/ingest/document` (field `file`)
   **Then** the backend extracts text (PDF via `pypdf`, DOCX via `python-docx`), chunks it with the existing `chunk_text`, embeds via `_embed_chunks`, and upserts into Pinecone namespace `{user_id}:knowledge` with `source="document"`, `source_id=<uuid>`, `title=<filename>` (FR39, FR40, NFR-S8)

2. **And** a `knowledge_sources` row is recorded with `source_type="document"`, `title=<filename>`, `source_url=null`, `page_count=<chunk count>`, `ingestion_status="completed"` (or `"failed"` on extraction error)

3. **And** unsupported file types are rejected with a **422** and oversize files with a **413** (reuse the BDD-upload `_MAX_UPLOAD_BYTES` guard pattern); an empty/unreadable document yields a clear error, not a silent success

4. **And** the ingested document appears in the existing "Ingested sources" list (source-type badge `document`) and its chunks are retrievable during verification — the RAG context panel (Story 4.4) renders it as a `document` source

5. **And** a new "Documents" option in the Knowledge Base UI lets the user pick a file, validates type (`.pdf`/`.docx`) and size **client-side** before uploading, and shows progress + success/error states consistent with the Confluence/Jira forms; the sources list refreshes after a successful upload

## Context & Critical Background

Extends the knowledge base with a **third ingestion source**. The chunk → embed → Pinecone pipeline and the `knowledge_sources` tracking table already exist (4.1/4.2); the UI shell exists (4.5). This story adds text **extraction** from binary docs and a multipart endpoint + a file-upload form.

| Reuse (do NOT reinvent) | Location |
|---|---|
| Chunking | `backend/app/services/vector_service.py::chunk_text` (already imported in `knowledge_service.py`) |
| Embedding | `backend/app/services/knowledge_service.py::_embed_chunks` |
| Pinecone upsert (with `title`/`url` metadata from 4.4) | `knowledge_service.py::_upsert_to_pinecone` |
| Multipart upload pattern (UploadFile, size guard, extension check) | `backend/app/api/v1/bdd.py::upload_bdd` |
| `KnowledgeSource` model + `KnowledgeSourceResponse` | `backend/app/models/knowledge_source.py`, `schemas/knowledge.py` |
| Ingested-sources list + panel | `frontend/src/components/knowledge/KnowledgeBasePanel.tsx` (4.5) |

## Tasks / Subtasks

### Backend — dependencies

- [x] **Task 1: Add PDF/DOCX parsing dependencies** (AC: 1)
  - [x] Add `pypdf` and `python-docx` to `backend/pyproject.toml` dependencies; `uv sync`
  - [x] `python-multipart` is already present (used by `bdd.py` upload) — no change

### Backend — extraction + service

- [x] **Task 2: Text extraction helpers in `knowledge_service.py`** (AC: 1, 3)
  - [x] `def _extract_pdf_text(data: bytes) -> str` using `pypdf.PdfReader(io.BytesIO(data))`, join page texts
  - [x] `def _extract_docx_text(data: bytes) -> str` using `docx.Document(io.BytesIO(data))`, join paragraph texts
  - [x] Raise a clear domain error (or return "") for empty/garbled extraction so the caller can emit a proper 422

- [x] **Task 3: `ingest_document()` service function** (AC: 1, 2)
  - [x] `async def ingest_document(user_id, filename, data: bytes, content_type: str, db) -> dict` returning `{"ingested_count": int, "chunk_count": int, "title": filename}`
  - [x] Dispatch on extension/content_type → `_extract_pdf_text` / `_extract_docx_text`; `chunk_text(...)`; `_embed_chunks(...)`; `_upsert_to_pinecone(source_id=<uuid4>, chunks, embeddings, namespace=f"{user_id}:knowledge", source="document", title=filename, url="")`
  - [x] Record a `KnowledgeSource(source_type="document", source_url=None, title=filename, page_count=len(chunks), ingestion_status="completed")`; on extraction failure record `ingestion_status="failed"` and raise so the route returns an error
  - [x] If `chunk_text` yields nothing (empty doc) → do not upsert; surface a 422-worthy error

### Backend — endpoint

- [x] **Task 4: `POST /api/v1/knowledge/ingest/document`** (AC: 1, 3)
  - [x] In `backend/app/api/v1/knowledge.py`: `file: UploadFile = File(...)`, `Depends(get_current_user)`, `Depends(get_db)`
  - [x] Validate `file.filename` ends with `.pdf` or `.docx` → else 422; read bytes; enforce a max-size guard (define `_MAX_DOC_BYTES`, e.g. 10 MB) → 413
  - [x] Call `knowledge_service.ingest_document(...)`; return a JSON body `{ingested_count, chunk_count, title}` (200). **Plain JSON, not SSE** — a single file is one source; the frontend shows a spinner, not a stream (design decision, see Dev Notes)
  - [x] Map extraction/empty-doc failures to 422 with an actionable message

- [x] **Task 5: Backend tests** (AC: 1, 2, 3)
  - [x] `backend/tests/test_document_ingestion.py`: `_extract_pdf_text`/`_extract_docx_text` return text from tiny in-memory fixtures (build a minimal PDF/DOCX in-test, or mock the readers); `ingest_document` upserts with `source="document"` + records the source row; endpoint returns 200 for a valid `.pdf`; 422 for `.txt`; 413 for oversize; user-scoping preserved

### Frontend — hook + types

- [x] **Task 6: `useIngestDocument` hook + request/response types** (AC: 5)
  - [x] In `frontend/src/lib/types/knowledge.ts`: add `DocumentIngestResponse { ingested_count: number; chunk_count: number; title: string }`
  - [x] Add `useIngestDocument` to `useKnowledge.ts`: multipart POST via the shared `apiClient` (`FormData` with `file`; axios sets the boundary automatically; the JWT interceptor still applies). Expose `{ ingest(file: File), isUploading, error, result }`. (No SSE — normal request/response.)

### Frontend — UI

- [x] **Task 7: "Documents" card in `KnowledgeBasePanel`** (AC: 4, 5)
  - [x] Add a third card: a file `<input type="file" accept=".pdf,.docx">` + submit
  - [x] **Client validation:** reject files not ending `.pdf`/`.docx` and files over the size limit (mirror `_MAX_DOC_BYTES`) with an inline message before uploading
  - [x] While uploading: spinner + disabled submit; on success show "Ingested {ingested_count} chunk(s) from {title}" and call the `useKnowledgeSources` `refetch()`; on error show the inline rose alert (reuse `ErrorNote`)
  - [x] `ingested_count === 0` → the same informational degraded note used for Confluence/Jira (AC6 pattern from 4.5)

- [x] **Task 8: Frontend tests** (AC: 5)
  - [x] Extend `KnowledgeBasePanel.test.tsx` (mock `useIngestDocument`): selecting a `.txt` file blocks submit + shows a validation message; a valid `.pdf` triggers `ingest`; success result renders the count; error renders the alert

## Dev Notes

### Design decision — plain JSON, not SSE (AC1)

Confluence/Jira ingestion stream SSE because they process **many** pages/tickets. A document upload is **one** source (even if it chunks into many vectors), and multipart upload already requires the full body up-front. So the document endpoint returns a simple JSON response and the UI shows a spinner — simpler than mixing multipart + SSE, and consistent enough with the other forms' success/error UX. (If per-chunk streaming is wanted later, it can be added without changing the storage model.)

### Design decision — extract-and-discard (no original-file storage)

RAG only needs the extracted **text chunks** in Pinecone. This story does **not** store the original PDF/DOCX in Supabase Storage — `source_url` is `null`. That keeps scope tight and avoids storage/ACL work. See open question Q1 if you want originals retained for download later.

### Extraction specifics

- **PDF:** `pypdf.PdfReader(io.BytesIO(data))`; iterate `reader.pages`, `page.extract_text()`, join with `\n`. Scanned/image PDFs yield little/no text → treat empty extraction as a 422 ("no extractable text — is this a scanned image?").
- **DOCX:** `docx.Document(io.BytesIO(data))`; join `p.text for p in doc.paragraphs`. (Tables/headers are out of scope for MVP.)
- Both are synchronous/CPU-bound — wrap in `asyncio.to_thread(...)` to avoid blocking the event loop, consistent with how Pinecone calls are offloaded in `knowledge_service.py`.

### Pinecone / embeddings note

Reuse `_upsert_to_pinecone` **as-is** (it already stores `title`/`url` metadata from Story 4.4) — pass `source="document"`, `title=filename`, `url=""`. Retrieval (`query_knowledge_base` → `_chunk_from_match`) already returns these, and the RAG panel (4.4) renders unknown-source items by `source_id`/title, so `document` sources display without frontend changes. Embeddings require `LLM_PROVIDER=openai` + `LLM_API_KEY` (else random dev vectors) and `PINECONE_API_KEY` for real storage — same as the rest of Epic 4.

### Multipart upload pattern to mirror

[bdd.py::upload_bdd](backend/app/api/v1/bdd.py) shows the exact pattern: `file: UploadFile = File(...)`, `raw = await file.read()`, size check → 413, extension check → 422. Reuse it; define `_MAX_DOC_BYTES` (10 MB is reasonable for PDFs).

### Frontend multipart with apiClient

`apiClient` is axios with a JWT interceptor — send `FormData` and let axios set the `Content-Type: multipart/form-data; boundary=…` header (do **not** hand-set it). Unlike the SSE ingest hooks (raw fetch), this is a normal request, so `apiClient` is the right tool.

### Project Structure Notes

**Backend — modify:** `pyproject.toml` (deps), `knowledge_service.py` (extraction + `ingest_document`), `api/v1/knowledge.py` (endpoint)
**Backend — create:** `backend/tests/test_document_ingestion.py`
**Frontend — modify:** `lib/types/knowledge.ts` (response type), `lib/hooks/useKnowledge.ts` (`useIngestDocument`), `components/knowledge/KnowledgeBasePanel.tsx` (Documents card), `components/knowledge/__tests__/KnowledgeBasePanel.test.tsx`
**Do NOT modify:** the Confluence/Jira ingest paths; `_upsert_to_pinecone`/`_embed_chunks`/`query_knowledge_base` internals; the RAG display panel (already source-agnostic)

### Testing Standards

- Backend: `pytest`/`pytest-asyncio`; build minimal in-memory PDF/DOCX fixtures or mock `pypdf.PdfReader`/`docx.Document`. Mock `_embed_chunks` + Pinecone as in `test_rag_verification.py`. Full-suite regression + ruff-clean on new code.
- Frontend: Vitest + `@testing-library/react`; mock `useIngestDocument`. For file input, use `fireEvent.change(input, { target: { files: [new File([...], "x.pdf", { type: "application/pdf" })] } })`. Keep changed files eslint + tsc clean.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.6 (FR39, FR40; NFR-S8)
- [Source: backend/app/api/v1/bdd.py:88-137] — multipart UploadFile + size/extension guard pattern
- [Source: backend/app/services/knowledge_service.py] — `_embed_chunks`, `_upsert_to_pinecone`, `chunk_text` import, namespace convention
- [Source: backend/app/api/v1/knowledge.py] — where the new endpoint + `GET /sources` (4.5) live
- [Source: backend/app/models/knowledge_source.py] / [schemas/knowledge.py] — `KnowledgeSource` / `KnowledgeSourceResponse` (reuse)
- [Source: frontend/src/components/knowledge/KnowledgeBasePanel.tsx] — add the Documents card here (4.5)
- [Source: frontend/src/lib/hooks/useKnowledge.ts] — add `useIngestDocument`; `useKnowledgeSources.refetch()` to refresh
- [Source: _bmad-output/implementation-artifacts/4-4-rag-context-display-panel.md] — RAG panel is source-agnostic (renders `document` with no changes); MagicMock test caveat
- [Source: _bmad-output/implementation-artifacts/4-5-knowledge-base-ingestion-ui.md] — panel/validation/degraded-note patterns to mirror

### Open Questions (non-blocking)

- **Q1 — Retain originals?** This story extract-and-discards (no Supabase Storage of the source file; `source_url=null`). If users should be able to re-download the uploaded doc later, add Storage upload + a signed-URL `source_url` — a follow-up story.
- **Q2 — Scanned PDFs / OCR?** Image-only PDFs yield no text and are rejected with a 422. OCR (e.g. Tesseract) is explicitly out of scope.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New deps installed via `uv sync`: `pypdf 6.14.2`, `python-docx 1.2.0` (+ `lxml`).
- New test file kept ruff-clean (wrapped long f-string in the 413 handler into a `max_mb` local; combined a nested `with` into a single statement; extracted long inline dict/assert to locals).
- Pre-existing `useKnowledge.ts` `_useSSEIngest` lint/type debt (4 `rules-of-hooks` errors + 1 `fetch` overload tsc error) is unchanged — line numbers shifted by the new hook but no new issues introduced (verified the tsc error at line 124 is the shifted pre-existing `fetch` call, not the new `useIngestDocument`).

### Completion Notes List

- **Extraction (AC1/AC3):** `_extract_pdf_text` (pypdf) and `_extract_docx_text` (python-docx) run under `asyncio.to_thread` (CPU-bound); corrupt files raise `DocumentIngestError`. Image-only PDFs yield no text → `chunk_text` returns `[]` → `NO_EXTRACTABLE_TEXT` (422), and a `failed` `knowledge_sources` row is recorded so the user sees the attempt.
- **Pipeline reuse:** `ingest_document` reuses `chunk_text` → `_embed_chunks` → `_upsert_to_pinecone(source="document", title=filename, url="")`. No changes to the Confluence/Jira paths or the Pinecone helpers.
- **Endpoint (AC1/AC3):** `POST /knowledge/ingest/document` mirrors the BDD-upload guard pattern — extension check → 422, `_MAX_DOC_BYTES` (10 MB) → 413, `DocumentIngestError` → 422. Returns plain JSON `{ingested_count, chunk_count, title}` (no SSE — a single file is one source, per the design decision).
- **Display is free (AC4):** the RAG context panel (4.4) and the 4.5 sources list are source-agnostic — `document` sources render with no display changes (badge = `document`, no link since `source_url` is null).
- **Frontend (AC5):** `useIngestDocument` posts `FormData` via `apiClient` (JWT interceptor applies). The Documents card validates extension + 10 MB size client-side before uploading, shows a spinner, then a chunk-count success note or an inline error; `refetch()` refreshes the sources list on success.
- **Validation:** Backend **214 pass** (+11), changed backend files ruff-clean. Frontend `KnowledgeBasePanel` suite **17 pass** (+5); my changed files eslint + tsc clean (only the pre-existing `useKnowledge.ts` debt remains, untouched).

### File List

**Backend — modified:**
- `backend/pyproject.toml` — added `pypdf`, `python-docx` deps
- `backend/app/services/knowledge_service.py` — `DocumentIngestError`, `_extract_pdf_text`, `_extract_docx_text`, `ingest_document`
- `backend/app/api/v1/knowledge.py` — `POST /ingest/document` endpoint + `_MAX_DOC_BYTES`
- `backend/app/schemas/knowledge.py` — `DocumentIngestResponse`

**Backend — created:**
- `backend/tests/test_document_ingestion.py` — 11 tests (extraction, service, endpoint 200/422/413)

**Frontend — modified:**
- `frontend/src/lib/types/knowledge.ts` — `DocumentIngestResponse`
- `frontend/src/lib/hooks/useKnowledge.ts` — `useIngestDocument` hook
- `frontend/src/components/knowledge/KnowledgeBasePanel.tsx` — Documents upload card
- `frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx` — +5 document tests

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-04 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 5 ACs implemented; extraction, 422/413 guards, and source-agnostic display verified.

### Action Items — all resolved

- [x] **[M1][Med]** `ingest_document` embedded **all** chunks in one `_embed_chunks` call with no batching and no error handling → a large PDF could exceed OpenAI's per-request input/token limits, raising a non-`DocumentIngestError` that surfaced as an **unhandled 500**. Fixed: embed in batches of `_EMBED_BATCH_SIZE` (100) and wrap embed+upsert in try/except → on failure, record a `failed` source row and raise `DocumentIngestError` (clean 422). Added 2 tests (batch-count assertion + graceful-failure). `[knowledge_service.py]`
- [x] **[L1][Low]** Success/error note persisted after upload; selecting a new file didn't clear it → added a `reset()` to `useIngestDocument`, called on file-input change. `[useKnowledge.ts, KnowledgeBasePanel.tsx]`
- [x] **[L2][Low]** Endpoint buffered the whole file before the size check → added an up-front `file.size` pre-check (413) before `read()`, keeping the post-read check as defense-in-depth. `[knowledge.py]`

**Post-fix validation:** backend **216 pass** (+2), changed backend files ruff-clean. Frontend panel suite **17 pass**; changed files eslint + tsc clean (pre-existing `useKnowledge.ts` `_useSSEIngest` debt untouched).

## Change Log

- 2026-07-04: Implemented Story 4.6 — Document Upload Knowledge Ingestion (PDF/DOCX). Added text extraction (pypdf/python-docx), an `ingest_document` service reusing the chunk→embed→Pinecone pipeline with `source="document"`, a multipart `POST /knowledge/ingest/document` endpoint (422/413 guards), and a Documents upload card in the Knowledge Base UI with client-side validation. RAG display + sources list required no changes (source-agnostic). Backend 214 tests pass (+11); frontend panel suite 17 (+5).
- 2026-07-04: Code review (adversarial) — Approve. Resolved 1 Medium + 2 Low: batched large-document embeds + graceful failure (M1), clear stale upload note on new file selection (L1), up-front size pre-check (L2). Backend 216 pass; frontend 17 pass.
