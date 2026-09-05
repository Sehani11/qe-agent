# Story 6.7: Manual Training Dataset Upload

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ui-redesign-feature-file.md)): The "sky-blue accent system" this story inherits from `KnowledgeBasePanel` is gone; both panels now share the pass/fail/pending signal tokens. The dataset list renders `SkeletonRows` while loading (the literal "Loading datasets…" text no longer exists — its test asserts `role="status"`), and upload/delete outcomes raise a toast alongside the retained inline per-file rejection reasons.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **researcher**,
I want to upload my own `.feature` files or JSONL datasets through the app,
so that a fine-tune can be trained now rather than waiting months for captured corrections to accumulate.

## Acceptance Criteria

1. **Given** an authenticated user on the training-data page
   **When** they upload one or more `.feature` files and/or `.jsonl` datasets in a single request
   **Then** each file is validated **independently**, stored in Supabase Storage under a dedicated `training-data` folder via the existing `StorageService`, and recorded in a `training_datasets` row scoped to that user — with the response reporting per-file accepted/rejected outcomes rather than failing the whole batch

2. **And** an uploaded `.feature` file is rejected with a clear message when it contains no usable scenario, where "usable" is decided by **the same parser and quality rules `training/build_dataset.py` applies** — not a second, looser check written for the UI

3. **And** an uploaded `.jsonl` file is validated line by line against the training-pair shape, and a rejection names **the first offending line number and what was wrong with it**, never a generic failure

4. **And** the page lists what has been uploaded (filename, kind, scenario/pair count, upload date) and supports deleting an entry, removing both the database row and the stored object — mirroring the ingested-sources list from Story 4.7

5. **And** `training/build_dataset.py` can read this uploaded corpus as a third source alongside `--features-dir` and `--from-db`, with `.jsonl` pairs bypassing back-generation (they are already complete pairs) while `.feature` uploads flow through it like any other document

6. **And** uploads are user-scoped: a user can only list and delete their own, enforced in the route layer (NFR-S8) — 403 for another user's row, 404 for missing or malformed ids

7. **And** each row carries `training_opt_in` stamped at write time from `TRAINING_DATA_OPT_IN`, and the builder excludes flagged-out rows — closing the hole that would otherwise let this page bypass the Story 6.6 consent control; the flag never appears in any API response

8. **And** backend Pytest tests cover accept, reject-invalid (both kinds), mixed batch, list, delete, and cross-user 403; frontend Vitest tests cover upload, list render, and delete

## Context & Critical Background

### Why this story exists: the corpus is currently **zero**, not small

Story 6.6 ran the builder against the live database and found:

```
seen 2 · opted out 0 · incomplete steps 2 · kept 0
```

Both `uploaded` rows — the only human-authored Gherkin in the database — **fail the quality filter** because their scenarios lack a complete Given/When/Then. Story 6.5 cannot train on that. This story is the unblocker, so its job is not "let a user upload a file" — it is **"guarantee that what the user uploads is what the builder can actually use."**

### ⚠️ The single most important design constraint: one parser, not two

If the upload endpoint writes its own "does this look like a feature file?" check, this story ships a page that happily accepts files the builder silently drops — reproducing the exact failure above with a friendlier UI on top. **The accept/reject decision at upload time and the keep/drop decision at build time must be the same code.**

That forces a direction, because `training/` sits at the repo root **outside the backend Docker build context** (`backend/Dockerfile` ends with `COPY . .`, so only `backend/` ships). The backend cannot import `training/build_dataset.py` in production — but `build_dataset.py` **already imports from the backend** (`sys.path.insert(REPO_ROOT / "backend")`, then `from app.schemas.bdd import BDDGenerateResponse`).

So the shared code moves **into the backend**, and the builder imports it:

```
backend/app/services/training_data_service.py   ← parser + quality rules + jsonl validation
        ↑ imported by the upload route            ↑ imported by training/build_dataset.py
```

Extract from `build_dataset.py`: `Scenario`, `FeatureDoc`, the threshold constants (`MIN_SCENARIOS`, `MAX_SCENARIOS`, `MIN_STEP_CHARS`, `MAX_STEP_CHARS`, `MAX_FILE_CHARS`, the compiled regexes) and the bodies of `parse_feature` / `passes_quality`. Leave the DB, LLM back-generation, splitting and CLI behind — those stay in `training/`.

### Keep `--dry-run` reporting intact through the extraction

`parse_feature` and `passes_quality` currently mutate a `Stats` object, which is a `training/` concern the backend must not inherit. Change them to **return reasons** and let `build_dataset.py` map reasons onto its counters:

```python
# backend/app/services/training_data_service.py
def parse_feature(text: str, origin: str) -> tuple[FeatureDoc | None, str | None]:
    """Returns (doc, rejection_reason). Reason is one of REASON_* or None."""

def quality_reason(doc: FeatureDoc) -> str | None:
    """None when the document is usable; otherwise a REASON_* constant."""
```

`outlines_skipped` is currently a side effect of parsing — make it a counter field on `FeatureDoc` so the caller can still report it. Define the reasons as module constants (`REASON_TOO_LARGE`, `REASON_UNPARSABLE`, `REASON_NO_SCENARIOS`, `REASON_TOO_MANY_SCENARIOS`, `REASON_INCOMPLETE_STEPS`, `REASON_PLACEHOLDER_TEXT`) so `Stats` field names map mechanically and the UI can turn the same constant into a human message.

### What counts as a valid `.jsonl` line

The shape is the one `build_dataset.py` emits and the one `FineTunedModelProvider` will be served:

```json
{"messages": [{"role": "system", "content": "..."},
              {"role": "user", "content": "AC1: ..."},
              {"role": "assistant", "content": "{\"scenarios\": [...]}"}]}
```

Validate, in order, reporting the **1-based line number** on first failure:
1. the line parses as JSON and is an object
2. `messages` is a non-empty array of objects each having string `role` and `content`
3. at least one `user` and one `assistant` message exist
4. the assistant `content` parses as JSON and validates against `BDDGenerateResponse`, with a **non-empty** `scenarios` array

Step 4 is not optional strictness. An empty `scenarios` array *validates successfully* against the Pydantic model (the field defaults to `[]`) — `build_dataset.py` rejects it for exactly that reason, because it would teach the model to return nothing. And a pair whose assistant side is not `BDDGenerateResponse`-shaped trains a model the application cannot parse, breaking the contract Story 6.1 established. Say so in the rejection message so the researcher knows what to fix.

### Content lives in Storage; the database holds the record

The DB row stores metadata and the object path, not the bytes. That is what AC1 asks for, it avoids a second copy drifting from the first, and `bdd_files` already exists for *captured* content — this table is for *supplied* content. Two consequences the dev must handle deliberately:

- **On upload, Storage is not best-effort.** `bdd.py`'s `/upload` treats a Storage failure as a warning because PostgreSQL holds the content there. Here Storage *is* the content, so a `StorageServiceError` must fail the file with a clear message (502) and no row — a row pointing at nothing is worse than a failed upload. Upload the object **first**, then insert the row.
- **On delete, Storage is best-effort.** Mirror Story 4.7's vector deletion: attempt the object removal, log a warning if it fails, and delete the row regardless. A user must always be able to clear a broken entry.

### `.jsonl` pairs must skip back-generation

`build_dataset.py`'s pipeline is *Gherkin → LLM reconstructs the AC → pair*. An uploaded `.jsonl` **is already a pair** — sending it through `backgenerate()` would throw away the researcher's own input side and spend LLM calls to invent a worse one. Carry those records in a separate list and merge them in **after** back-generation, **before** `split_by_origin`, with `meta.origin` set to the uploaded file so origin-level splitting still keeps one file's pairs on one side. Also note `run()` currently does `docs = docs[: args.limit]` — decide and state whether `--limit` applies to ready pairs (recommend: yes, applied to the merged record list, so `--limit` keeps meaning "cap the corpus").

### Consent parity with Story 6.6 (AC7)

Story 6.6 established that every row destined for training carries its consent at capture time. A new table feeding the same builder without that flag is a hole in the guarantee — an operator running `TRAINING_DATA_OPT_IN=false` would find this page still supplying training data. One column, one condition, same pattern: `default=lambda: settings.training_data_opt_in` so it **fails closed**, `server_default=true()` for non-ORM inserts, and never exposed in a response body.

### The builder's WHERE clause is still string-concatenated

`build_db_queries()` was rewritten in Story 6.6 to accumulate conditions into a list joined by `AND` precisely because a second appended `WHERE` is invalid SQL. The new uploads query must follow the same shape, and its engine **must** pass `connect_args={"statement_cache_size": 0}` — Supabase's PgBouncer transaction pooler rejects prepared statements, and this was a live High finding in the previous story.

### Reuse map

| Piece | Location |
|---|---|
| Parser + quality rules to extract | `training/build_dataset.py` lines ~68–80, 110–135, 162–268 |
| Storage wrapper + folder constants | `app/services/storage_service.py` — add `FOLDER_TRAINING_DATA = "training-data"` |
| Multi-part upload route with size caps | `app/api/v1/knowledge.py::ingest_document` (lines 148–194) |
| List + delete + 404/403 route pattern | `app/api/v1/knowledge.py::list_sources`, `delete_source` (lines 32–82) |
| Model style (uuid pk, user_id index, created_at) | `app/models/knowledge_source.py` |
| Opt-in column style (fails closed) | `app/models/bdd_file.py` lines 47–62 |
| Migration style | `alembic/versions/e3f4a5b6c7d8_*.py` (create table), `c9d0e1f2a3b4_*.py` (current head) |
| Builder DB query composition | `training/build_dataset.py::build_db_queries`, `collect_db_rows` |
| Backend route tests (mocked DB, dep overrides) | `tests/test_knowledge_sources.py` |
| Builder tests (pure query functions) | `tests/test_build_dataset.py` |
| Frontend page shell | `src/app/knowledge/page.tsx` |
| Panel with upload + list + delete + ConfirmModal | `src/components/knowledge/KnowledgeBasePanel.tsx` |
| Query/mutation hooks | `src/lib/hooks/useKnowledge.ts` |
| Frontend test style (hook module mocked) | `src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx` |

## Tasks / Subtasks

- [x] **Task 1: Extract the shared parser into the backend** (AC: 2, 5)
  - [x] Create `backend/app/services/training_data_service.py` holding `Scenario`, `FeatureDoc`, the threshold constants and regexes, `parse_feature(text, origin) -> tuple[FeatureDoc | None, str | None]` and `quality_reason(doc) -> str | None`
  - [x] Add an `outlines_skipped: int = 0` field to `FeatureDoc` so the counter survives the move out of `Stats`
  - [x] Define `REASON_*` constants plus a `REASON_MESSAGES` mapping to user-facing text, so the API message and the builder's counter name come from one place
  - [x] Update `training/build_dataset.py` to import these and map returned reasons onto its `Stats` fields — **delete the duplicated definitions**, do not leave both
  - [x] Verify `--dry-run` output is unchanged for a known corpus (same counters, same numbers)

- [x] **Task 2: JSONL validation** (AC: 3)
  - [x] Add `validate_jsonl(text: str) -> int` to `training_data_service.py`, returning the pair count or raising `TrainingDataError(message, line_number)` on the first bad line, checking the four rules in order (JSON object → `messages` shape → user+assistant present → assistant content validates as `BDDGenerateResponse` with non-empty `scenarios`)
  - [x] Blank lines are skipped, not errors; a file with zero valid pairs is rejected
  - [x] Message must name the line number and the specific problem

- [x] **Task 3: Schema + migration** (AC: 1, 6, 7)
  - [x] `backend/app/models/training_dataset.py` — `TrainingDataset`: `id` (UUID pk), `user_id` (String, not null, indexed), `filename`, `kind` (`String(10)`: `"feature"` | `"jsonl"`), `item_count` (Integer, not null, default 0), `storage_path` (String, not null), `training_opt_in` (Boolean, not null, `default=lambda: settings.training_data_opt_in`, `server_default=true()`), `created_at`
  - [x] New Alembic revision creating `training_datasets` with the `ix_training_datasets_user_id` index; `down_revision = "c9d0e1f2a3b4"` — confirm with `alembic current` first, the DB is live
  - [x] `downgrade()` drops the index and the table
  - [x] Add `training_datasets` to `backend/supabase/migrations/001_rls_policies.sql` (`ENABLE ROW LEVEL SECURITY` + a `FOR ALL USING (auth.uid()::text = user_id)` policy) — that file is **not** run by Alembic; note in the story record that it needs a manual re-apply

- [x] **Task 4: Upload / list / delete API** (AC: 1, 3, 4, 6, 7)
  - [x] Add `FOLDER_TRAINING_DATA = "training-data"` to `storage_service.py` and document it in the module docstring's folder list
  - [x] New `backend/app/api/v1/training.py`, registered in `api.py` with prefix `/training`, tag `training`
  - [x] `POST /datasets` — `files: list[UploadFile]`, per-file validation, 10 MB cap per file checked from `file.size` **and** re-checked on the read bytes; reject non-`.feature`/`.jsonl` extensions and non-UTF-8 content per file
  - [x] Upload the object via `storage_service.upload_file(folder=FOLDER_TRAINING_DATA, path=f"{dataset_id}/{filename}", user_id=current_user)` **before** inserting the row; a `StorageServiceError` marks that file rejected with a clear message and writes no row
  - [x] Response `TrainingUploadResponse { accepted: list[TrainingDatasetResponse], rejected: list[{filename, reason}] }` — 200 when anything was accepted, 422 when every file was rejected
  - [x] `GET /datasets` — the caller's rows only, newest first
  - [x] `DELETE /datasets/{dataset_id}` — non-UUID id → 404 before touching the DB (Story 4.7 L2), missing → 404, other user's → 403, otherwise best-effort `storage_service.delete_file` (log and continue on failure) then delete the row, 204
  - [x] Schemas in `backend/app/schemas/training.py`; `training_opt_in` is **not** a field on any response model
  - [x] Keep validation and storage orchestration in the service layer — the route does auth, size limits and HTTP mapping only (mandatory rule 1)

- [x] **Task 5: Builder reads the uploaded corpus** (AC: 5, 7)
  - [x] Add `--from-uploads` (and optional `--uploads-user <user_id>` filter) to `build_dataset.py`; update the `parser.error` guard so any one of the three sources satisfies it
  - [x] `build_upload_queries(user_id)` as a **pure function** alongside `build_db_queries`, conditions accumulated and joined with `AND`, always including `training_opt_in = true`, parameterised (no interpolation)
  - [x] Create the engine with `connect_args={"statement_cache_size": 0}` — non-negotiable, see the background note
  - [x] Download each object through `storage_service.download_file`; a storage failure prints and continues (this source is optional, like `--from-db`)
  - [x] `kind="feature"` → parse via the shared parser, `origin=f"upload:{id}:{filename}"`, joins `docs` for back-generation
  - [x] `kind="jsonl"` → parse into records that **skip** back-generation, stamping `meta.origin` with the same value, merged before `split_by_origin`
  - [x] Count uploaded-but-excluded rows into `Stats.opted_out` and add a `ready_pairs` field (`Stats.render()` picks new fields up automatically)
  - [x] Decide and document how `--limit` applies to the merged record list
  - [x] Update `training/README.md`: the third source, the JSONL shape and why it bypasses back-generation, and that the parser now lives in the backend

- [x] **Task 6: Frontend page** (AC: 1, 3, 4)
  - [x] `src/lib/types/training.ts` — `TrainingDataset`, `TrainingUploadResponse`, `RejectedFile` (no `any`, mandatory rule 8)
  - [x] `src/lib/hooks/useTrainingData.ts` — `useTrainingDatasets` (query), `useUploadTrainingDatasets` (mutation posting `FormData` with repeated `files` entries), `useDeleteTrainingDataset` (mutation + `invalidateQueries`); all through `apiClient`, never raw fetch (mandatory rule 6)
  - [x] `src/components/training/TrainingDataPanel.tsx` — multi-file input (`accept=".feature,.jsonl"`, `multiple`), client-side extension/size pre-check, in-flight loader (mandatory rule 9), per-file accepted/rejected result list showing the backend's reason verbatim (that is where the line number lives), uploaded-datasets list with filename / kind badge / item count / date, delete button behind `ConfirmModal`
  - [x] `src/app/training/page.tsx` — page shell mirroring `app/knowledge/page.tsx`
  - [x] Add a "Training Data" link to the home page nav in `src/app/page.tsx`

- [x] **Task 7: Tests** (AC: 8)
  - [x] `backend/tests/test_training_datasets.py` — accepted `.feature` (row written, storage called, `item_count` = kept scenario count); `.feature` with an incomplete Given/When/Then rejected with the shared parser's reason and **no row**; valid `.jsonl` accepted with the pair count; `.jsonl` with a bad line 3 rejected quoting line 3; mixed batch → one accepted + one rejected in one response; oversize → rejected; list scoped to the caller; delete 204 + storage delete + row delete; delete 403 cross-user; 404 missing; 404 malformed uuid; `training_opt_in` stamped from the setting on both values and absent from every response body
  - [x] `backend/tests/test_training_data_service.py` — the extracted parser still rejects what it rejected before (incomplete steps, placeholder text, outline-only, oversize) and `validate_jsonl` reports the right line number for each of the four failure modes
  - [x] `backend/tests/test_build_dataset.py` — extend: uploads query excludes `training_opt_in = false`, single `WHERE`, parameterised, user filter applied; jsonl records bypass back-generation
  - [x] `frontend/src/components/training/__tests__/TrainingDataPanel.test.tsx` — upload calls the hook with the chosen files, rejected reasons render, list renders rows and the empty state, delete asks for confirmation then calls the mutation
  - [x] Mock the hook module wholesale, as `KnowledgeBasePanel.test.tsx` does

- [x] **Task 8: Apply migration + regression**
  - [x] `uv run alembic upgrade head` against the live DB, then confirm the table, index and column defaults exist
  - [x] Re-apply `001_rls_policies.sql` (or run just the two new statements) so the new table is not left without RLS — **see the finding below: RLS is not enabled on ANY table in this database, so the file has evidently never been applied here. Left consistent rather than partially enabled; flagged instead.**
  - [x] `uv run --directory backend pytest -q` → **321 baseline** + new, zero regressions
  - [x] `ruff check app tests` — zero new errors
  - [x] `npx vitest run` and `npx tsc --noEmit` in `frontend/`
  - [x] Smoke-check the loop end to end: store a real `.feature` and `.jsonl` through the service against live Supabase Storage + the live DB, then run `build_dataset.py --from-uploads` and confirm the file is **kept**, not discarded — this is the whole point of the story

## Dev Notes

### Why a new table rather than reusing `bdd_files`

`bdd_files` is session-scoped capture of what the application produced or a user corrected; every row has a `session_id` and belongs to a pipeline run. A manually supplied corpus has no session, no ticket and no acceptance criteria on its own. Forcing it into `bdd_files` would mean a nullable `session_id` and a fourth `source` value that breaks the `content_format` invariants Story 6.4 documented. Separate table, same consent column.

### Endpoint shape

```
POST   /api/v1/training/datasets        multipart: files[]  → {accepted: [...], rejected: [{filename, reason}]}
GET    /api/v1/training/datasets                            → [TrainingDataset]
DELETE /api/v1/training/datasets/{id}                       → 204
```

Per-file outcomes rather than all-or-nothing: a researcher dropping twenty `.feature` files should not lose nineteen good ones to one bad one, and the rejection reasons are the feedback that makes the corpus better.

### Frontend integration traps

- **Responses stay `snake_case`.** `architecture.md:280` claims the axios interceptor transforms JSON keys to camelCase. It does not — [client.ts](frontend/src/lib/api/client.ts) only attaches the Bearer token. Every existing type (`KnowledgeSource.page_count`, `created_at`) is snake_case, so `TrainingDataset` must be too. Trust the code, not the doc.
- **`apiClient` defaults to `Content-Type: application/json`.** For the multipart upload, override it per request exactly as `useIngestDocument` does: `apiClient.post(url, form, { headers: { "Content-Type": "multipart/form-data" } })`.
- **Multiple files:** append repeatedly under the same key — `files.forEach(f => form.append("files", f))` — which is what FastAPI's `files: list[UploadFile] = File(...)` expects.
- **Visual language:** reuse the panel/card, badge, error-note and result-note treatments from `KnowledgeBasePanel.tsx` rather than inventing new ones; the sky-blue accent system and the `ConfirmModal` destructive flow are already established.

### Anti-patterns for this story

- ❌ **Don't write a second feature-file validator in the backend.** If the UI's rules and the builder's rules can drift, they will, and the symptom is a corpus that looks full and builds empty
- ❌ Don't leave the old parser in `build_dataset.py` "for safety" after extracting it — two copies is the failure this story exists to prevent
- ❌ Don't run uploaded `.jsonl` pairs through `backgenerate()`
- ❌ Don't treat a Storage failure as non-fatal on upload (it is fatal here) or as fatal on delete (it is not)
- ❌ Don't expose `training_opt_in` in any response — operator configuration, not user data (Story 6.6 precedent)
- ❌ Don't append a second `WHERE` to any builder query
- ❌ Don't add training dependencies to `backend/pyproject.toml` — the Dockerfile's `COPY . .` would ship them. Nothing in this story needs a new backend dependency
- ❌ Don't touch `bdd.py`, `bdd_service.py` or `bdd_model/**`

### Project Structure Notes

**Backend — create:** `app/models/training_dataset.py`, `app/schemas/training.py`, `app/api/v1/training.py`, `app/services/training_data_service.py`, one `alembic/versions/*.py`, `tests/test_training_datasets.py`, `tests/test_training_data_service.py`.
**Backend — modify:** `app/api/v1/api.py` (router), `app/services/storage_service.py` (folder constant + docstring), `supabase/migrations/001_rls_policies.sql`, `tests/test_build_dataset.py`.
**Training — modify:** `training/build_dataset.py` (import the shared parser, new source, new queries), `training/README.md`.
**Frontend — create:** `src/app/training/page.tsx`, `src/components/training/TrainingDataPanel.tsx`, `src/components/training/__tests__/TrainingDataPanel.test.tsx`, `src/lib/hooks/useTrainingData.ts`, `src/lib/types/training.ts`.
**Frontend — modify:** `src/app/page.tsx` (nav link).
**No changes to `.env.example`, `docker-compose.yml` or `cfn/` — this story adds no environment variable.**

### Testing Standards

Backend: `pytest` + `pytest-asyncio` (`asyncio_mode="auto"`), DB mocked with `AsyncMock`, `get_current_user` and `get_db` overridden via `app.dependency_overrides` — see `tests/test_knowledge_sources.py`. Patch `app.api.v1.training.storage_service` (or the service module's reference) rather than reaching into the Supabase SDK. Monkeypatch the opt-in setting the way Story 6.6 does, which requires `settings` to be read at call time, not import time.

Frontend: Vitest + Testing Library, no `test` script in `package.json` — run `npx vitest run`. Mock `@/lib/hooks/useTrainingData` wholesale and drive component state through mutable module-level mock objects, as `KnowledgeBasePanel.test.tsx` does.

`training/` sits outside the backend package and is not covered by `ruff check app tests`; the builder was made ruff-clean in Story 6.6, so keep it that way by eye. Builder logic that must not regress belongs in `backend/tests/` so it runs with the normal suite — that was a Story 6.6 review finding.

### Environment notes

Alembic connects via `DIRECT_DATABASE_URL` (port 5432), not the transaction pooler on 6543 — that split is why DDL applies cleanly. Any ad-hoc verification script must pass `connect_args={"statement_cache_size": 0}` or it hits `DuplicatePreparedStatementError` through PgBouncer. Storage operations need `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`; without them `StorageService.is_available` is false and every operation raises `STORAGE_NOT_CONFIGURED` — surface that as a clean error, not a 500.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 6, Story 6.7 and the execution-order note (6.7 unblocks 6.5)
- [Source: _bmad-output/planning-artifacts/architecture.md] — Mandatory rules 1, 4, 6, 8, 9, 10; naming and structure patterns
- [Source: _bmad-output/implementation-artifacts/6-6-training-data-opt-out-control.md] — write-time consent stamping, the `WHERE`-composition trap, the PgBouncer engine guard, and the finding that the usable corpus is currently zero
- [Source: _bmad-output/implementation-artifacts/6-4-training-data-capture.md] — `content_format` / `parent_id` capture model this table deliberately does not reuse
- [Source: _bmad-output/implementation-artifacts/4-7-delete-knowledge-sources.md] — best-effort external cleanup, 404-for-malformed-id, ConfirmModal delete flow
- [Source: training/build_dataset.py] — parser, quality thresholds, `build_db_queries`, `split_by_origin`, `Stats`
- [Source: training/README.md] — corpus sourcing, quality filters, the two invariants worth keeping
- [Source: backend/app/api/v1/knowledge.py:32-82,148-194] — list/delete and multipart-upload route patterns
- [Source: backend/app/services/storage_service.py] — folder namespacing and `{user_id}/{folder}/{path}` scoping
- [Source: backend/supabase/migrations/001_rls_policies.sql] — manual RLS application step

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Backend **321 → 371 passed** (+50), zero regressions. Ruff clean on every file this story touched, including `training/build_dataset.py`.
- Frontend **13 new tests, all passing**. Full suite 122 passed / 2 failed — both failures **pre-existing**, verified by stashing this story's work and re-running (`auth.test.ts::signUp` and `BDDEditorPanel::renders the editor panel`). `tsc --noEmit` reports exactly 1 error, also pre-existing (`useKnowledge.ts:147`, Story 4.5).
- Red-green observed on every task: parser tests failed with `ModuleNotFoundError: app.services.training_data_service`; the 20 API tests failed with 404s before the router existed; the 9 builder tests failed on `bd.build_upload_queries` not existing.
- Parser extraction verified behaviour-preserving against a 4-file corpus: `seen 4 · outlines skipped 1 · incomplete steps 1 · placeholder text 1 · kept 2`, and the `And` connective still survives inside the Given text.
- Migration applied for real: `c9d0e1f2a3b4` → **`d0e1f2a3b4c5`**. Confirmed `training_datasets` exists with `training_opt_in boolean NOT NULL DEFAULT true`, `item_count NOT NULL DEFAULT 0` and `ix_training_datasets_user_id`.
- **End-to-end against live infrastructure**, not mocks: stored a real `.feature` and a real `.jsonl` through `store_dataset` into Supabase Storage + the live DB, then ran `build_dataset.py --from-uploads --uploads-user … --dry-run` → `seen 2 · ready pairs 2 · kept 1`, with the `.feature` **kept** and the `.jsonl` pairs reported as *"2 uploaded pairs already complete (skipping back-generation)"*.
- Consent filter verified on the new table for real: flagging both rows `training_opt_in=false` produced `seen 0 · opted out 2` and the explicit "EXCLUDED, not missing" message.
- Cleanup verified through the real delete path: both rows and both stored objects removed; `training_datasets` left empty.

### Completion Notes List

- **AC1 — per-file outcomes.** `POST /training/datasets` takes `files: list[UploadFile]` and validates each independently, returning `{accepted, rejected}`. A mixed batch keeps the good files; 422 only when nothing at all was accepted, and even then the body carries the per-file reasons. Objects land at `{user_id}/training-data/{dataset_id}/{filename}` through `StorageService`.
- **AC2 — one parser, not two.** This was the spine of the story. `parse_feature`, the quality thresholds and the JSONL rules moved out of `training/build_dataset.py` into `app/services/training_data_service.py`, and the builder now imports them. The duplicated definitions were **deleted**, not left behind. The upload endpoint and the dataset builder now reach the same verdict by construction, which is what stops the Story 6.6 failure (a corpus that looks full and builds empty) from recurring through this page. `parse_feature`/`quality_reason` return `REASON_*` constants whose values equal the builder's `Stats` field names, so `--dry-run` reporting survived the move intact — pinned by a test.
- **AC3 — line-numbered JSONL errors.** Validation runs four checks in order and raises `TrainingDataError(message, line_number)` on the first failure. The assistant side must parse as `BDDGenerateResponse` with a non-empty `scenarios` array — an empty array *validates* against the model (the field defaults to `[]`) and would quietly train the model to return nothing.
- **AC4 — list and delete.** Mirrors Story 4.7: newest-first list, delete behind a `ConfirmModal`, object removal best-effort so a broken entry can always be cleared, row always removed.
- **AC5 — third builder source.** `--from-uploads` (with optional `--uploads-user`) reads `training_datasets` and downloads each object. `.feature` uploads flow through back-generation; `.jsonl` uploads **skip it** and merge after back-generation but before `split_by_origin`, carrying `meta.origin` set to the uploaded file so one file's pairs never straddle the holdout boundary. `--limit` caps both the documents sent for back-generation and the final merged corpus.
- **AC6 — user scoping.** List filters on `user_id`; delete returns 403 for another user's row and 404 for missing or malformed ids (the non-UUID check runs before the DB is touched, so a bad id cannot surface as a 500).
- **AC7 — consent parity.** The epic's ACs did not mention it, which would have left this page as a route around the Story 6.6 opt-out. `training_opt_in` is stamped at write time, defaults to the configured setting so it **fails closed**, is excluded by the builder, and never appears in a response body.
- **Scope held.** No changes to `bdd.py`, `bdd_service.py` or `bdd_model/**`. No new backend or frontend dependencies. No new environment variables, so `.env.example`, `docker-compose.yml` and `cfn/` are untouched.

### ⚠️ Finding: RLS is not enabled on any table in this database

Adding `training_datasets` to `001_rls_policies.sql` prompted a check of the live database:

```
bdd_files False · chat_messages False · knowledge_sources False
sessions  False · verification_results False · training_datasets False
```

**`001_rls_policies.sql` has evidently never been applied to this database** — not just for the new table. Per-user isolation is currently enforced only in the route layer (which every endpoint does check, and which this story's tests cover). The practical exposure is low because the backend uses the service-role key, which bypasses RLS anyway; the risk is that the defence-in-depth layer the architecture assumes is not actually there.

I deliberately did **not** enable RLS on `training_datasets` alone — a single table with row security while its six siblings have none is an inconsistent posture that invites a false sense of coverage. The SQL file is correct and complete; applying it is a one-command operation that deserves its own decision:

```bash
psql $DIRECT_DATABASE_URL -f backend/supabase/migrations/001_rls_policies.sql
```

### ⚠️ Note for Story 6.5: the corpus is still empty

This story built the mechanism, not the data. `training_datasets` currently holds **0 rows** (the smoke-test rows were cleaned up). Before 6.5 can train anything, someone has to actually upload a corpus through the new page — or point `--features-dir` at cloned Cucumber/SpecFlow repositories, which remains the fastest route to volume.

### File List

**Backend — created:**
- `backend/app/services/training_data_service.py` — shared parser, quality rules, JSONL validation, upload/delete orchestration
- `backend/app/models/training_dataset.py` — `TrainingDataset` model
- `backend/app/schemas/training.py` — `TrainingDatasetResponse`, `RejectedFile`, `TrainingUploadResponse`
- `backend/app/api/v1/training.py` — upload / list / delete routes
- `backend/alembic/versions/d0e1f2a3b4c5_create_training_datasets_table.py` — migration
- `backend/tests/test_training_data_service.py` — 21 tests (parser + JSONL validation)
- `backend/tests/test_training_datasets.py` — 20 tests (API)

**Backend — modified (review follow-up):**
- `backend/app/services/training_data_service.py` — `reject_unsafe_filename()`, `pair_fingerprint()`
- `backend/tests/test_training_data_service.py` — 13 filename-safety and fingerprint tests
- `backend/tests/test_training_datasets.py` — 7 traversal / storage-path tests

**Training — modified (review follow-up):**
- `training/build_dataset.py` — `--limit` pushed into the uploads SQL, `.jsonl` pair de-duplication, strict UTF-8 decode
- `backend/tests/test_build_dataset.py` — 6 tests for limit, de-duplication and corrupt objects

**Frontend — modified (review follow-up):**
- `frontend/src/components/training/TrainingDataPanel.tsx` — clears the file selection after a successful upload
- `frontend/src/components/training/__tests__/TrainingDataPanel.test.tsx` — 2 re-upload tests

**Repo — modified (review follow-up):**
- `.gitignore` — `__pycache__/` and `*.py[cod]`
- `training/__pycache__/build_dataset.cpython-312.pyc` — **untracked** (`git rm --cached`)

**Backend — modified:**
- `backend/app/api/v1/api.py` — `/training` router registration
- `backend/app/models/__init__.py` — export `TrainingDataset`
- `backend/app/services/storage_service.py` — `FOLDER_TRAINING_DATA` + docstring
- `backend/supabase/migrations/001_rls_policies.sql` — RLS enable + isolation policy for `training_datasets`
- `backend/tests/test_build_dataset.py` — 9 tests for the uploaded-corpus source

**Training — modified:**
- `training/build_dataset.py` — imports the shared parser (duplicates deleted), `build_upload_queries`, `process_upload_rows`, `collect_uploads`, `--from-uploads` / `--uploads-user`, `Stats.ready_pairs`, ready-pair merge
- `training/README.md` — parser location, the third source, JSONL bypass, consent coverage

**Frontend — created:**
- `frontend/src/lib/types/training.ts`
- `frontend/src/lib/hooks/useTrainingData.ts`
- `frontend/src/components/training/TrainingDataPanel.tsx`
- `frontend/src/components/training/__tests__/TrainingDataPanel.test.tsx` — 13 tests
- `frontend/src/app/training/page.tsx`

**Frontend — modified:**
- `frontend/src/app/page.tsx` — "Training Data" nav link

## Senior Developer Review (AI)

**Date:** 2026-08-08 · **Outcome:** Changes Requested → **all findings resolved** · **Reviewer model:** claude-opus-5

**Git vs File List:** 1 discrepancy — `training/__pycache__/build_dataset.cpython-312.pyc` was modified but undocumented (resolved as M4). **AC audit:** 7 of 8 fully implemented on first pass; AC6 was PARTIAL (see H1). **Task audit:** all `[x]` verified — `grep` confirms no `parse_feature`, `passes_quality`, `Scenario`, `FeatureDoc` or threshold constants remain in `build_dataset.py`, so AC2's "delete the duplicates" claim is genuine rather than merely asserted.

### Action Items

- [x] **[High] H1 — A crafted filename escaped the user's storage namespace, and chained into cross-user deletion.** The object key was built as `f"{dataset_id}/{filename}"` with `filename` taken straight from the multipart `Content-Disposition` header, and `validate_upload` only checked the extension. Verified: `"../../../victim-user/owned.feature"` normalised to `victim-user/owned.feature` — outside `{user_id}/training-data/`, and therefore outside the storage RLS policy, which keys off the first path segment being the owner. The chain was worse than the write: that path was then persisted as the attacker's *own* `storage_path`, so deleting their own row passed the ownership check and removed the **victim's** object. Directly contradicted AC6. **Fixed:** `reject_unsafe_filename()` refuses any name containing a path separator, or equal to `.`/`..`/empty, and is called both in `validate_upload` and again in `store_dataset` — the line that actually builds the key does not rely on a caller elsewhere having checked. 13 tests, plus live verification that the traversal is refused before any storage write while the happy path still lands in-namespace.
- [x] **[Med] M1 — `--limit` was silently ignored by the uploads source.** `build_db_queries` pushes `LIMIT` into SQL; `build_upload_queries` took no limit at all, so `--from-uploads --limit 10` selected every row and **downloaded every object from Storage** before `run()` trimmed the list in memory. Every returned row costs a network round-trip, so trimming afterwards is pure waste. **Fixed:** limit pushed into SQL, with the exclusion counter still ignoring it (the operator wants the true total excluded). 3 tests.
- [x] **[Med] M2 — Uploaded `.jsonl` pairs were never de-duplicated, while `.feature` uploads were.** `process_upload_rows` kept a fingerprint set but applied it only to `FeatureDoc`s, so re-uploading the same dataset entered every pair twice. Duplicate examples are not neutral — they reweight the fine-tune toward whatever was duplicated. **Fixed:** `pair_fingerprint()` added alongside `FeatureDoc.fingerprint` in the shared service; duplicates are skipped and counted into `Stats.duplicates`. This also corrected an existing test that had asserted three *identical* lines produce three records — its intent was the back-generation bypass, so its fixture now uses three distinct pairs.
- [x] **[Med] M3 — The upload form never cleared its selection, so a second click re-uploaded the same batch.** Combined with M2, one accidental double-click doubled a JSONL corpus and created duplicate rows and duplicate stored objects. **Fixed:** the selection and the file input are cleared on success — but only when something was accepted, so an all-rejected batch stays visible for the user to inspect. 2 tests, including one asserting the selection *survives* a fully-rejected upload.
- [x] **[Med] M4 — A compiled artifact was tracked in git and undocumented.** `training/__pycache__/build_dataset.cpython-312.pyc` changed on every run of this story's work. **Fixed:** `git rm --cached` plus `__pycache__/` and `*.py[cod]` added to the root `.gitignore` — `training/` sits outside `backend/`, so it was not covered by the backend's ignore rules. Zero `.pyc` files are tracked now.
- [x] **[Low] L1 — A test name overclaimed.** `test_oversize_file_is_rejected_without_reading_it_all` never asserted anything about not reading the body. **Fixed:** renamed to `..._and_never_stored` and given the assertion that actually matters — neither the storage write nor the row happens.
- [x] **[Low] L3 — `errors="replace"` silently corrupted damaged objects.** Content is UTF-8 validated at upload, so a decode failure at build time means the stored object is damaged — precisely when substituting replacement characters into training data is worst. **Fixed:** decodes strictly, reports the object and counts it as unparsable. 1 test.

### Deferred (not fixed)

- **[Low] L2 — No explicit cap on the number of files per request.** Starlette's implicit 1000-file default is the only bound, so one request can still carry ~10 GB across the per-file 10 MB limit. Worth a cap, but it is a general upload-hardening concern shared with `/knowledge/ingest/document`, not something this story introduced.
- **[Low] L4 — No UI signal when `TRAINING_DATA_OPT_IN=false`.** Uploads are accepted and stored, then excluded from every build, with no hint to the user. Fixing it properly means exposing the setting through an endpoint, which is a small feature rather than a correction.

### Review Notes

The backend logic, the parser extraction and the consent plumbing all held up — the extraction in particular was verified behaviour-preserving rather than taken on trust. Every finding clustered at the **boundaries**: the client-supplied filename that becomes a storage key (H1), the source that reads back from Storage (M1, M2, L3), and the form that feeds it (M3). H1 is the one that mattered: the story's own AC6 promised user scoping and the route layer delivered it for database rows, while the storage layer quietly did not, and the delete path turned that gap into a cross-user write primitive. Worth noting the same `f"{id}/{filename}"` pattern exists at [bdd.py:249](backend/app/api/v1/bdd.py#L249) from Epic 1 and has the same weakness — out of scope here, but it should not survive much longer.

## Change Log

- 2026-08-08: Addressed code review findings — 7 resolved (1 High, 4 Medium, 2 Low), 2 Low deferred with rationale. The High was a path traversal: a crafted upload filename became the storage object key unchecked, escaping `{user_id}/training-data/` past the storage RLS policy and — because that path was persisted as the uploader's own `storage_path` — turning a legitimate delete of their own row into deletion of another user's object. Filenames are now refused unless they are plain names, checked both at validation and again at the line that builds the key. Also pushed `--limit` into the uploads SQL so the builder stops downloading a corpus it will discard, de-duplicated uploaded `.jsonl` pairs (which had been kept while `.feature` uploads were deduped), stopped the upload form re-sending a batch on a second click, made a corrupt stored object report instead of silently mangling, and untracked a `.pyc` that had been committed. Backend **371 → 397** tests, frontend **13 → 15**, zero regressions, zero new lint errors.
- 2026-08-08: Implemented Story 6.7. Researchers can now upload `.feature` files and `.jsonl` training pairs at `/training`, and `build_dataset.py --from-uploads` consumes them. The central change is that the Gherkin parser and quality rules moved out of `training/build_dataset.py` into `backend/app/services/training_data_service.py`, so the upload endpoint accepts exactly what the builder keeps — the duplicated copy was deleted rather than left to drift. Uploaded `.jsonl` pairs skip back-generation and merge before the origin-level split. Added `training_opt_in` to the new table so this page cannot bypass the Story 6.6 consent control, which the epic's ACs had not covered. Migration `d0e1f2a3b4c5` applied and verified against the live database, and the whole loop (store → build → delete) was exercised against real Supabase Storage. Backend **321 → 371** tests, 13 new frontend tests, zero regressions, zero new lint errors. Surfaced that `001_rls_policies.sql` has never been applied to this database — no table has RLS enabled.
- 2026-08-08: Story drafted. Made the shared-parser extraction the spine of the story: `training/build_dataset.py`'s Gherkin parser and quality rules move into `backend/app/services/training_data_service.py` so the upload endpoint's accept/reject decision is literally the builder's keep/drop decision — without that, this page would accept files the builder silently discards, which is exactly how the current corpus reached zero usable rows. Specified the JSONL contract down to the `BDDGenerateResponse` validation and non-empty `scenarios` check, added consent parity with Story 6.6 (the epic's ACs omit it, leaving a route around the opt-out), inverted the Storage failure policy per direction (fatal on upload, best-effort on delete), and required uploaded `.jsonl` pairs to bypass back-generation.
