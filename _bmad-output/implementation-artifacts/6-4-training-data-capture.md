# Story 6.4: Training Data Capture

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-training-data-loop.md)): `scenariosToGherkin()` was emitting steps WITHOUT Given/When/Then keywords, so every `edited` and `uploaded` row this story captured was unparsable by the dataset builder ("incomplete steps") and every downloaded `.feature` file was unrunnable. Fixed forward only — rows written before 2026-08-23 stay unparsable. `parse_feature` now also reads back the `# Source AC:` comments, so clause attribution survives a round trip through the editor.


Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **researcher**,
I want every BDD generation to persist its input acceptance criteria and any human corrections,
so that a dataset of real story-to-test-case pairs accumulates from normal usage.

## Why This Ships Before The Rest Of Epic 6

`prd.md:168` specifies a model *"trained on **real story-to-test-case pairs**"*. Today the application stores **neither half of that pair usably**:

| Signal | State today |
|---|---|
| Acceptance criteria that drove a generation | ❌ Discarded — [session.py:19](backend/app/models/session.py#L19) keeps only `jira_ticket_id` |
| Resulting Gherkin | ⚠️ Stored, but format varies by source (see below) |
| Human corrections in the editor | ❌ Never leave the browser — [bdd.py](backend/app/api/v1/bdd.py) has only `/generate` and `/upload` |

**This data cannot be captured retroactively.** Jira tickets are edited after the fact, and a correction not recorded at the moment of editing is gone forever. Stories 6.5, 6.2 and 6.3 all depend on this, but only this one loses value every day it waits — which is why it is exempt from Epic 6's "Phase 2" framing.

## Acceptance Criteria

1. **Given** a BDD generation request succeeds
   **When** the resulting `bdd_files` row is written
   **Then** the acceptance criteria text that produced it is persisted **on that row**, forming a self-contained input→output pair

2. **And** a user can save edited BDD content from the editor, producing a new `bdd_files` row with `source='edited'` that links to the generated row it came from — both versions survive, the original is never overwritten

3. **And** the link to the originating generated row is resolved **server-side** (most recent `source='generated'` row for that session), because the client has no way to learn the row id — see the `BDDGenerateResponse` trap in Dev Notes

4. **And** `bdd_files` records which format its `content` column holds, because the column is already heterogeneous today: `generated` rows hold JSON, `uploaded` rows hold raw Gherkin, and nothing distinguishes them. Existing rows are backfilled

5. **And** existing `generated` and `uploaded` behaviour is unchanged — no change to any response shape, no regression to Epic 1 flows

6. **And** an Alembic migration adds the new columns, all nullable, with existing rows backfilled and a working `downgrade()`

7. **And** per-user isolation is preserved: the save endpoint requires auth and returns 403 when the session belongs to another user, mirroring `/bdd/upload`

8. **And** tests verify: AC persisted on generate, an edited save creates a distinct row while the generated row survives intact, parent resolution picks the right row, 403 on foreign session, and oversized payloads rejected

## Context & Critical Background

### Three discoveries that shape the design

**1. `bdd_files.content` is already heterogeneous — and nothing says so.**
[bdd.py:64](backend/app/api/v1/bdd.py#L64) writes `json.dumps(response.model_dump())` for `generated`, while [bdd.py:142](backend/app/api/v1/bdd.py#L142) writes raw Gherkin text for `uploaded`. Any consumer has to guess from `source`. Story 6.5's dataset builder parses Gherkin, so it would silently find nothing usable in `generated` rows. AC4 fixes this latent inconsistency rather than working around it.

**2. Edited content is Gherkin, and it carries its own traceability.**
The editor operates on `bddContent` (Gherkin text), produced client-side by [`scenariosToGherkin()`](frontend/src/lib/hooks/useBDDGenerate.ts#L25), which emits `# Source AC: <clause>` comments above each scenario. So an edited save is Gherkin **with the AC clause mapping preserved in comments** — 6.5 can recover `source_ac_clause` from them. Persist the editor content verbatim; do not try to re-structure it server-side.

**3. There is no RLS in this repository.**
No `CREATE POLICY`, no row-level-security statement anywhere in `backend/`. Per-user isolation is enforced **in the route layer** — see the ownership check at [bdd.py:136](backend/app/api/v1/bdd.py#L136). The epic's AC mentions RLS; the accurate requirement is AC7 above. Adding columns to an existing table needs no policy change regardless, since Postgres policies are per-table.

### Reuse map — do not invent new patterns

| Piece | Location |
|---|---|
| Ownership check + 403 | `api/v1/bdd.py:124-137` (upload) — mirror exactly |
| Size guard | `api/v1/bdd.py:_MAX_UPLOAD_BYTES` (512 KB) — reuse the constant |
| Migration style | `alembic/versions/a7b8c9d0e1f2_*.py` — `op.add_column`, nullable, story named in docstring |
| ORM model | `models/bdd_file.py` |
| Mutation hook shape | `lib/hooks/useBDDUpload.ts` — copy for `useBDDSave` |
| Editor state | `bddContent` / `setBddContent` via `useSessionContext()` |
| Backend test patterns | `tests/test_bdd.py` — `_make_upload_db_mock`, `override_auth` fixture |

## Tasks / Subtasks

- [x] **Task 1: Schema — migration + model** (AC: 1, 2, 4, 6)
  - [x] New Alembic revision, `down_revision = "a7b8c9d0e1f2"` (current head — verify with `alembic heads` before writing)
  - [x] Add to `bdd_files`, all `nullable=True` so the migration is safe on existing rows:
    - `acceptance_criteria` `Text` — the input half of the pair
    - `content_format` `String(10)` — `'json'` | `'gherkin'`
    - `parent_id` `UUID` — the generated row an edit derives from
  - [x] Backfill in `upgrade()`: `UPDATE bdd_files SET content_format = CASE WHEN source = 'generated' THEN 'json' ELSE 'gherkin' END`
  - [x] `downgrade()` drops all three columns
  - [x] Mirror the columns in [models/bdd_file.py](backend/app/models/bdd_file.py); extend the `source` comment to `"generated" | "uploaded" | "edited"`
  - [x] **Do NOT** add a DB `CHECK` constraint on `source` — none exists today and adding one would need a data audit first

- [x] **Task 2: Persist the input half on generate** (AC: 1, 4, 5)
  - [x] In [bdd.py:61-66](backend/app/api/v1/bdd.py#L61-L66) set `acceptance_criteria=request.acceptance_criteria` and `content_format="json"`
  - [x] In the upload handler set `content_format="gherkin"`
  - [x] **Response shapes must not change** — `BDDGenerateResponse` and `BDDUploadResponse` stay exactly as they are (AC5)

- [x] **Task 3: `POST /api/v1/bdd/save`** (AC: 2, 3, 7, 8)
  - [x] Request schema `BDDSaveRequest` in `schemas/bdd.py`: `{session_id: UUID, content: str}` — **no `parent_id` from the client**
  - [x] Resolve parent server-side: latest `bdd_files` row for this `session_id` with `source='generated'`, ordered by `created_at DESC`. `None` is valid (user edited an uploaded file) — store `parent_id=None`, do not error
  - [x] Copy `acceptance_criteria` from the resolved parent onto the new row so each row is a self-contained pair
  - [x] Persist `source='edited'`, `content_format='gherkin'`, content verbatim
  - [x] Enforce ownership: session must exist and belong to `current_user` → else 403. Unlike `/upload`, do **not** create a session on the fly — saving an edit to a non-existent session is a client bug, return 404
  - [x] Reject payloads over `_MAX_UPLOAD_BYTES`
  - [x] Response `BDDSaveResponse`: `{id, session_id, source, created_at}`

- [x] **Task 4: Backend tests** (AC: 8)
  - [x] `tests/test_bdd.py` — extend, following the existing fixtures
  - [x] Generate persists `acceptance_criteria` and `content_format='json'`
  - [x] Save creates a row with `source='edited'`, `content_format='gherkin'`, `parent_id` = the latest generated row, and inherits its `acceptance_criteria`
  - [x] Save with no prior generated row → `parent_id is None`, still 200
  - [x] Save on another user's session → 403, nothing written
  - [x] Save on unknown session → 404, nothing written
  - [x] Oversized content → 413
  - [x] Unauthenticated → 401

- [x] **Task 5: Frontend — save affordance** (AC: 2)
  - [x] `lib/hooks/useBDDSave.ts` — mutation posting `{session_id, content}`, modelled on `useBDDUpload.ts`
  - [x] Add a **Save** button to [BDDEditorPanel.tsx](frontend/src/components/pipeline/BDDEditorPanel.tsx) alongside Upload/.feature/CSV, using the same button styling
  - [x] Track dirty state: enabled only when `bddContent` differs from the last saved/loaded value; disabled while in flight; brief saved confirmation
  - [x] Reuse the existing error-banner pattern (`uploadError`) rather than adding a second mechanism
  - [x] Works on the mobile textarea branch too — it is the same `bddContent`

- [x] **Task 6: Frontend test**
  - [x] Extend `__tests__/BDDEditorPanel.test.tsx`: Save disabled when clean, enabled after edit, calls the hook with current content, error banner on failure
  - [x] ⚠️ **Baseline first** — this file already has **1 failing test** (`monaco-editor-mock` testid, 2 pass / 1 fail). It is pre-existing and NOT yours. Record the before/after counts; do not "fix" it as part of this story, and do not let it mask a real regression

- [x] **Task 7: Regression**
  - [x] Backend: `uv run --directory backend pytest -q` → **290 baseline** + new, zero regressions
  - [x] Backend lint: `ruff check` — 11 pre-existing errors exist in `config.py`, `bdd_service.py`, `test_bdd_model_factory.py`; introduce **zero** new
  - [x] Frontend: `npx vitest run` (**there is no `test` script in package.json**) and `npx eslint` on changed files

## Dev Notes

### ⚠️ The `BDDGenerateResponse` trap (why AC3 exists)

The obvious design — return the new row's id from `/bdd/generate` so the client can send it back as `parent_id` — is **wrong here**. `BDDGenerateResponse.model_json_schema()` is passed to the model as `response_format` at [bdd_service.py:58](backend/app/services/bdd_service.py#L58). Any field added to that model becomes part of the schema handed to the LLM, which will then try to populate it. Story 6.1 hit this exact trap and documented it.

Resolving the parent server-side sidesteps it completely: no response shape changes, no new field on a model that doubles as an LLM contract.

### Schema rationale

`acceptance_criteria` goes on **`bdd_files`, not `sessions`**. A session can regenerate after its Jira ticket has been edited; if the AC lived on the session, older rows would silently re-point at text that no longer produced them. Per-row storage costs some duplication and buys pairs that stay correct forever — the entire point of the story.

All columns nullable: historic rows have no AC and never will. `content_format` is backfilled because it is *derivable* for existing rows; `acceptance_criteria` is not, and must stay NULL rather than being guessed.

### Anti-patterns for this story

- ❌ Don't overwrite the generated row on save — the generated/edited **pair** is the training signal; losing the original destroys it
- ❌ Don't add `acceptance_criteria` to `sessions`
- ❌ Don't change `BDDGenerateResponse` or `BDDUploadResponse`
- ❌ Don't re-structure edited Gherkin into JSON server-side — persist verbatim; the `# Source AC:` comments carry the traceability
- ❌ Don't add training/ML dependencies anywhere in `backend/`

### Project Structure Notes

**Backend — modify:** `app/models/bdd_file.py`, `app/api/v1/bdd.py`, `app/schemas/bdd.py`, `tests/test_bdd.py`. **Create:** one `alembic/versions/*.py`.
**Frontend — modify:** `src/components/pipeline/BDDEditorPanel.tsx`, `src/components/pipeline/__tests__/BDDEditorPanel.test.tsx`. **Create:** `src/lib/hooks/useBDDSave.ts`.
**Do NOT modify:** `app/services/bdd_service.py`, `app/services/bdd_model/**` (Story 6.1, complete), `training/**` (Story 6.5).

### Testing Standards

Backend `pytest` + `pytest-asyncio` (`asyncio_mode="auto"`), mock the DB with `AsyncMock` per `test_bdd.py`'s existing helpers, override `get_current_user`. Frontend `vitest` + Testing Library, mock the hook rather than the network.

### ⚠️ Known blocker

**The Supabase database is currently unreachable** — `asyncpg` reports `tenant/user postgres.qinhim… not found`, so the project is paused or deleted. The migration can be *written* and its SQL reviewed, but it cannot be *applied or verified* until the database is restored. Raise this before starting Task 1 rather than discovering it at `alembic upgrade head`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 6, Story 6.4
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-08-08.md] — why this story exists and why it ships early
- [Source: _bmad-output/planning-artifacts/prd.md:168] — "trained on real story-to-test-case pairs"
- [Source: backend/app/api/v1/bdd.py:124-146] — ownership check and persistence pattern to mirror
- [Source: backend/app/services/bdd_service.py:58] — the response_format / LLM-contract trap
- [Source: frontend/src/lib/hooks/useBDDGenerate.ts:25] — `scenariosToGherkin`, emits `# Source AC:` comments
- [Source: backend/alembic/versions/a7b8c9d0e1f2_add_source_ref_to_knowledge_sources.py] — migration house style
- [Source: _bmad-output/implementation-artifacts/6-1-bdd-model-provider-abstraction.md] — prior story; test/lint baselines and the schema trap

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Backend: **290 → 298 passed** (+8), zero regressions.
- Frontend `BDDEditorPanel.test.tsx`: **2 pass / 1 fail → 7 pass / 1 fail**. The single failure is the pre-existing `renders the editor panel and control buttons` (`monaco-editor-mock` testid not found — `next/dynamic` renders the loading fallback under vitest). Identical assertion, untouched, not fixed as it is out of scope.
- Red-green observed on all new backend tests: 7 of 8 failed before implementation. `test_save_bdd_returns_404_for_unknown_session` passed vacuously in the red phase because no `/save` route existed yet, so any request 404'd — it only became meaningful once the route was added.
- Alembic head verified by reading the full revision chain (`a7b8c9d0e1f2` is the tip; nothing revises it). Used the file chain rather than `alembic current`, which would need a live DB.
- Lint: `ruff` on the five touched backend files nets **zero new errors** — the one new `B008` (FastAPI `Depends` in a default, matching both sibling endpoints in the same file) is offset by an `E501` removed from `bdd_file.py`. The new migration is fully ruff-clean. ESLint clean on all changed frontend files; `tsc --noEmit` reports only a pre-existing error in `useKnowledge.ts`, none in changed files.

### Completion Notes List

- **AC1/AC4 — the input half is now stored (Task 2).** `/bdd/generate` writes `acceptance_criteria` and `content_format='json'` onto the same row as the output; `/bdd/upload` writes `content_format='gherkin'`. Neither response shape changed (AC5).
- **AC2/AC3 — corrections are captured without touching the LLM contract (Task 3).** `POST /api/v1/bdd/save` always INSERTs a `source='edited'` row and never updates the generated one, because the generated/edited *pair* is the training signal — overwriting would destroy it. The parent is resolved server-side (latest `source='generated'` for the session), which avoided returning the row id from `/generate`; that would have meant adding a field to `BDDGenerateResponse`, whose JSON schema is passed to the model as `response_format`, so the LLM would have been asked to populate it. Same trap Story 6.1 documented.
- **A real defect surfaced during the green phase.** `BDDSaveResponse` initially read `bdd_file.id` / `created_at` after `commit()`, which are Python-side column defaults materialised only at flush. `expire_on_commit=False` means production would have populated them, so this was a mock artifact rather than a live bug — but the endpoint now assigns both explicitly so the response never depends on flush timing.
- **AC6 — migration written, `NOT` applied.** See the blocker below.
- **AC7 — isolation preserved.** Ownership check mirrors `/upload` (403 on foreign session). It deliberately diverges in one respect: `/save` returns 404 for an unknown session rather than creating one on the fly, since an edit with no session to belong to is a client bug, not a supported flow.
- **AC8 — 8 backend tests + 5 frontend tests.** Backend covers AC persistence, format recording, parent linkage and inheritance, the no-parent case, 403, 404, 413, 401. Frontend covers dirty/clean state, payload correctness, clean-after-save (so unchanged content is not re-saved as a duplicate correction), and error handling that keeps the content dirty so a failed save is never mistaken for a persisted one.
- **Scope held.** No change to `bdd_service.py`, `bdd_model/**` (Story 6.1), or `training/**` (Story 6.5). No `CHECK` constraint added to `source`. No training dependencies anywhere in `backend/`.

### ✅ Migration applied and verified — 2026-08-08

The database was restored and `alembic upgrade head` ran cleanly against it (PostgreSQL 17.6), closing the release gate that was open at implementation time.

| Check | Result |
|---|---|
| `alembic_version` | `a7b8c9d0e1f2` → **`b8c9d0e1f2a3`** |
| `acceptance_criteria`, `content_format`, `parent_id` | present, all nullable |
| Backfill against 10 real rows | `generated → json` ×8, `uploaded → gherkin` ×2 |
| Rows left with NULL `content_format` | **0** |
| Rows with `acceptance_criteria` | 0 — correct; historic rows are deliberately left NULL rather than guessed |

Notes:
- Alembic connected via `DIRECT_DATABASE_URL` (port 5432) rather than the transaction pooler (6543), which is the correct split for Supabase and why the DDL applied without incident.
- `downgrade()` remains **unexercised**. It is written and reviewed, but reverting on a live database was not worth the risk given the new columns hold only derived data.
- Corpus note for Story 6.5: the database holds just **2 `uploaded` rows** — the only human-authored Gherkin currently available. The dataset pipeline's weighting toward external `.feature` corpora is therefore the right call.

### File List

**Backend — created:**
- `backend/alembic/versions/b8c9d0e1f2a3_add_training_capture_to_bdd_files.py` — adds `acceptance_criteria`, `content_format`, `parent_id`; backfills `content_format`

**Backend — modified:**
- `backend/app/models/bdd_file.py` — three new nullable columns + rationale
- `backend/app/api/v1/bdd.py` — AC/format persisted on generate and upload; new `POST /save`
- `backend/app/schemas/bdd.py` — `BDDSaveRequest`, `BDDSaveResponse`
- `backend/tests/test_bdd.py` — 8 tests

**Frontend — created:**
- `frontend/src/lib/hooks/useBDDSave.ts` — save mutation hook

**Frontend — modified:**
- `frontend/src/components/pipeline/BDDEditorPanel.tsx` — Save button, keystroke-based dirty state, save handler
- `frontend/src/components/pipeline/__tests__/BDDEditorPanel.test.tsx` — `useBDDSave` mock (required, or the existing tests break), 6 new tests, `any` removed per Mandatory Rule 8

**Docs — modified (review follow-up):**
- `training/README.md` — `content_format` table and the JSON/Gherkin pair-asymmetry warning for Story 6.5

## Senior Developer Review (AI)

**Date:** 2026-08-08 · **Outcome:** Changes Requested → **all High and Medium resolved** · **Reviewer model:** claude-opus-5

**Git vs File List:** 0 discrepancies across all 8 claimed files. **Task audit:** every `[x]` task had real supporting evidence, and the unapplied-migration caveat was disclosed rather than glossed.

### Action Items

- [x] **[High] H1 — Save let non-corrections into the dataset, defeating the story's purpose.** `lastSavedContent` started `null` while `bddContent` is populated *externally* — on generation ([page.tsx:299](frontend/src/app/session/[sessionId]/page.tsx#L299)) and on session load ([page.tsx:185-190](frontend/src/app/session/[sessionId]/page.tsx#L185-L190)). Neither updated the baseline, so Save was enabled on untouched model output; one click wrote a `source='edited'` row with `parent_id` set and AC inherited — downstream **indistinguishable from a genuine human correction**, and worse than useless for preference training where chosen would equal rejected. Reloading a session re-armed it, so duplicates compounded. **Fixed:** dirtiness now derives from actual keystrokes (`hasUserEdited`, set only in `handleEditorChange`, cleared on save and on upload) rather than from a content comparison. The test that asserted the old behaviour as intended was replaced by `disables Save for untouched generated content` + `enables Save once the user actually edits the content`.
- [x] **[Med] M1 — AC8's "generated row survives intact" was untested.** Insert-only code implied it, but a refactor to an upsert would have passed every existing test. **Fixed:** `test_save_bdd_leaves_the_generated_row_intact` asserts exactly one row is written, that it is not the parent object, and that the parent's content, source and AC are unmutated.
- [x] **[Med] M2 — Parent lookup was not scoped by `user_id`.** Not exploitable given the preceding session-ownership check, but the parent's acceptance criteria are copied onto a row owned by the caller, and every other user-data query in this codebase scopes by user. **Fixed:** added `BddFile.user_id == current_user` to the parent query, pinned by `test_save_bdd_scopes_parent_lookup_to_the_calling_user`.
- [x] **[Med] M3 — Parent/child format asymmetry was undocumented.** The generated parent holds JSON, the edited child holds Gherkin, so a `(parent, child)` DPO pair cannot be diffed without rendering one side — and `build_dataset.py` has no JSON branch, silently treating `generated` rows as unparsable. **Fixed:** documented in `training/README.md` with the format table, the concrete gap, and a pointer to `scenariosToGherkin()` as the canonical rendering to port rather than reinvent.
- [x] **[Low] L1 — `justSaved` timer leaked.** `window.setTimeout` with no cleanup set state on unmounted components. **Fixed incidentally:** the timer was removed entirely; "Saved" now clears on the next edit, which is both leak-free and more accurate.
- [ ] **[Low] L2 — `parent_id` has no index.** Story 6.5 will query pairs by it and it exists solely to be joined on. **Not fixed:** deliberately deferred to 6.5, which will know the actual query shape. No FK either, consistent with a codebase that has none.

### Review Notes

The backend design held up — server-side parent resolution correctly avoided the `BDDGenerateResponse` trap, ownership mirrored `/upload`, and the migration is sound. Every finding clustered in the frontend dirty-state model, from one root cause: dirtiness was inferred by comparing content instead of observing user intent. Since `bddContent` is owned by the page and not the panel, that comparison could never distinguish "the model wrote this" from "the human wrote this" — which is precisely the distinction the story exists to record.

## Change Log

- 2026-08-08: Migration `b8c9d0e1f2a3` applied and verified against the restored database — `alembic_version` advanced from `a7b8c9d0e1f2`, all three columns present and nullable, and the backfill correctly classified all 10 existing rows (8 `json`, 2 `gherkin`, none left NULL). Release gate closed; the story's last unverified acceptance criterion is now confirmed against real data.
- 2026-08-08: Addressed code review findings — 5 items resolved (1 High, 3 Medium, 1 Low), 1 Low deferred to Story 6.5. Reworked the editor's dirty-state model so Save reflects actual keystrokes rather than a content comparison, closing a defect that let untouched model output be saved as a human correction and silently poison the dataset. Added the missing "generated row survives intact" test, scoped the parent lookup by `user_id`, and documented the JSON/Gherkin pair asymmetry in `training/README.md` for Story 6.5. Backend **298 → 300** tests, frontend BDDEditorPanel **7 → 8** passing (same 1 pre-existing failure); zero net new lint errors.
- 2026-08-08: Implemented Story 6.4. Training pairs are now captured from normal usage: `/bdd/generate` persists the acceptance criteria alongside its output, and a new `POST /api/v1/bdd/save` records human corrections as `source='edited'` rows linked to the generated row they came from, with the original always preserved. Added `content_format` to resolve the pre-existing heterogeneity of `bdd_files.content`, backfilled for existing rows. Parent linkage is resolved server-side to avoid adding a field to `BDDGenerateResponse`, whose schema doubles as the LLM's `response_format`. Frontend gained a Save button with dirty-state tracking and `useBDDSave`. Backend **290 → 298** tests, frontend BDDEditorPanel **2 → 7** passing (1 pre-existing failure unchanged); zero new lint errors. **Migration is written but unapplied — the Supabase database is unreachable.**
- 2026-08-08: Story drafted from the approved Sprint Change Proposal. Established baselines (backend 290 passing; `BDDEditorPanel.test.tsx` 2 pass / 1 pre-existing failure) and documented three discoveries that shape the design: `bdd_files.content` is already format-heterogeneous, edited Gherkin carries AC traceability in `# Source AC:` comments, and this repo enforces per-user isolation in the route layer rather than via RLS.
