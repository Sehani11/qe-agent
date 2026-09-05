# Story 4.7: Delete Ingested Knowledge Sources

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): Delete is scoped to the active project's namespace. `knowledge_sources.project_id` records where a row's vectors live — without it, a listing across projects would offer deletes reaching into a namespace the row does not belong to.


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-delete-all-knowledge-sources.md)): Added a bulk `DELETE /api/v1/knowledge/sources` (no id) returning `{deleted: N}`, surfaced as a "Delete all" button in the Ingested sources header behind its own `ConfirmModal`. It is NOT a loop over the per-source delete: it wipes the user's whole Pinecone namespace in one call, which also clears vectors this story could never target — legacy rows with no `source_ref`, and orphans left by an earlier failed deletion.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to delete a knowledge source I previously ingested,
so that outdated or incorrect project knowledge no longer influences verification or project Q&A.

## Acceptance Criteria

1. **Given** an authenticated user has ingested knowledge sources
   **When** they delete a source (with confirmation)
   **Then** `DELETE /api/v1/knowledge/sources/{id}` removes the source's vectors from `{user_id}:knowledge` **and** deletes the `knowledge_sources` row, returning **204**

2. **And** only the owner can delete — **404** for a missing source, **403** for another user's source (NFR-S8)

3. **And** vector deletion targets exactly that source's chunks, via a persisted `source_ref` (the Pinecone vector-id prefix = Confluence page id / Jira ticket id / document uuid), stored on ingest for all three source types

4. **And** a vector-store failure is logged but does **not** block removing the DB row (best-effort; the row always disappears so the user isn't stuck) — may leave orphan vectors, logged

5. **And** the ingested-sources list updates immediately after deletion (`["knowledge","sources"]` invalidated), with a per-row pending state and a confirmation prompt

## Context & Critical Background

The knowledge base (4.1 Confluence, 4.2 Jira, 4.6 documents) upserts chunks to Pinecone namespace `{user_id}:knowledge` with vector ids **`f"{source}_{source_id}_chunk_{i}"`** and metadata `{source, source_id, text, title, url}`, and tracks each source in `knowledge_sources`. 4.5 lists them. There was no delete.

### The crux: the DB row didn't carry the Pinecone id

`knowledge_sources` stored `source_type`, `source_url`, `title` — but **not** the `source_id` used in the vector ids (page id / ticket id / doc uuid). Without it, a source's vectors can't be targeted. Fix: add a **`source_ref`** column that stores exactly that id, populated on ingest. Deletion then targets `f"{source_type}_{source_ref}_chunk_*"`.

### Vector deletion across index kinds

Pinecone id-prefix listing (serverless) and metadata-filtered delete (pod) are mutually exclusive per index kind, so the service tries both: **A)** `index.list(prefix=...)` → `index.delete(ids=...)`; on failure **B)** `index.delete(filter={source, source_id}, ...)`. The whole thing is best-effort (AC4).

### Reuse map

| Piece | Location |
|---|---|
| Ownership 404/403 pattern | `reports.py` / `chat.py::_verify_session_owner` (mirrored in the delete route) |
| Namespace + Pinecone client | `knowledge_service` (`{user_id}:knowledge`, `pinecone.Pinecone(...)`) |
| Sources list + `refetch` | `useKnowledgeSources` / `KnowledgeBasePanel` "Ingested sources" |
| Alembic migration convention (`op.add_column`) | `backend/alembic/versions/*.py` (schema); `supabase/migrations/*.sql` is RLS/storage only |

## Tasks / Subtasks

### Backend

- [x] **Task 1: `source_ref` column** (AC: 3) — `KnowledgeSource.source_ref: str | None` + Alembic migration `a7b8c9d0e1f2_add_source_ref_to_knowledge_sources.py` (revises head `f5a6b7c8d9e0`)
- [x] **Task 2: populate `source_ref` on ingest** (AC: 3) — confluence `page.id`, jira `ticket.ticket_id`, document `source_id` (uuid) on the success rows
- [x] **Task 3: `knowledge_service` deletion** (AC: 1, 3, 4)
  - [x] `delete_source_vectors(user_id, source_type, source_ref)` — no-op without pinecone key / ref; strategy A (prefix list+delete) then B (filter delete); never raises (best-effort, logged)
  - [x] `delete_knowledge_source(source, db)` — delete vectors then the row + commit
- [x] **Task 4: `DELETE /api/v1/knowledge/sources/{id}`** (AC: 1, 2) — load row → 404/403 → `delete_knowledge_source` → 204
- [x] **Task 5: tests** (AC: 1–4) — 204 + row+vectors deleted (vector call asserted); 404 missing; 403 non-owner (no delete); `delete_source_vectors` no-op without ref/pinecone; swallows backend errors

### Frontend

- [x] **Task 6: `useDeleteKnowledgeSource`** (AC: 5) — `useMutation` → `DELETE /knowledge/sources/{id}`, invalidate `["knowledge","sources"]`
- [x] **Task 7: delete button in `KnowledgeBasePanel`** (AC: 5) — trash button per source row, confirmation via the reusable `ConfirmModal` (destructive), per-row pending spinner (`isPending && variables === id`)
- [x] **Task 8: panel test** — deletes after confirm (`mutate(id)`), does nothing when cancelled

## Dev Notes

### source_ref backfill / limitation

New ingests store `source_ref`; rows created before this migration keep it `NULL` → their vectors can't be targeted (the DB row is still deletable, AC4 best-effort). Acceptable — the alternative (re-deriving ids) isn't possible for documents (random uuid).

### Ownership at the app layer

`knowledge_sources` isn't in `001_rls_policies.sql` (added in Epic 4 after it); the route enforces `source.user_id == current_user` (mirrors `list_sources`' user-scoped query and the reports routes). No RLS change in this story.

### Route/DB note

`select(KnowledgeSource).where(KnowledgeSource.id == source_id)` with a non-UUID string could 500 at the DB layer for a malformed id — identical to the existing reports/sessions routes; left consistent (missing → 404 via `scalar_one_or_none`).

### Project Structure Notes

**Backend — modify:** `app/models/knowledge_source.py`, `app/services/knowledge_service.py`, `app/api/v1/knowledge.py`, `tests/test_knowledge_sources.py`. **Backend — create:** `alembic/versions/a7b8c9d0e1f2_add_source_ref_to_knowledge_sources.py` (apply with `uv run alembic upgrade head`).
**Frontend — modify:** `src/lib/hooks/useKnowledge.ts`, `src/components/knowledge/KnowledgeBasePanel.tsx`, `.../__tests__/KnowledgeBasePanel.test.tsx`.
**Do NOT modify:** ingestion/retrieval logic, `query_knowledge_base`, the RLS SQL for other tables.

### Testing Standards

Backend `pytest` with mocked DB + patched `delete_source_vectors`/`asyncio.to_thread`; assert 204/404/403 and that vectors + row are removed. Frontend `vitest` with `window.confirm` spied. Full-suite regression; new backend code ruff-clean.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.7
- [Source: backend/app/services/knowledge_service.py] — upsert id format `{source}_{source_id}_chunk_{i}`, namespace `{user_id}:knowledge`; new delete fns
- [Source: backend/app/models/knowledge_source.py] — `source_ref` column
- [Source: backend/app/api/v1/knowledge.py] — DELETE route (ownership)
- [Source: backend/alembic/versions/a7b8c9d0e1f2_add_source_ref_to_knowledge_sources.py] — column migration
- [Source: frontend/src/components/knowledge/KnowledgeBasePanel.tsx] — sources list + delete button
- [Source: frontend/src/lib/hooks/useKnowledge.ts] — `useDeleteKnowledgeSource`

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New backend code ruff-clean. `source_ref` is additive/nullable — no impact on `KnowledgeSourceResponse` (not exposed) or existing list tests.
- Pre-existing `useKnowledge.ts` issues (1 tsc fetch-overload + `_useSSEIngest` rules-of-hooks eslint) confirmed present on baseline (stash) — untouched by this story.

### Completion Notes List

- **Targeted vector deletion (AC1/AC3):** added `source_ref` (Pinecone id prefix), populated on all three ingest paths; `delete_source_vectors` removes `f"{source_type}_{source_ref}_chunk_*"` via prefix-list (serverless) with a metadata-filter fallback (pod).
- **Ownership (AC2):** route returns 404 (missing) / 403 (non-owner) before deleting; `test_delete_source_403_for_non_owner` asserts no DB delete occurs.
- **Best-effort (AC4):** `delete_source_vectors` is a no-op without a pinecone key or `source_ref`, and swallows/logs backend errors so the DB row is always removed (`test_delete_source_vectors_swallows_backend_errors`).
- **Frontend (AC5):** `useDeleteKnowledgeSource` invalidates `["knowledge","sources"]`; per-row trash button with a confirm prompt and pending spinner. Tests cover confirm→mutate and cancel→no-op.
- **Validation:** backend **255 pass** (+5 delete tests), new code ruff-clean. Frontend `KnowledgeBasePanel` suite **19 pass** (+2); full frontend suite shows only the 3 pre-existing unrelated failing files, unchanged.

### File List

**Backend — modified:**
- `backend/app/models/knowledge_source.py` — `source_ref` column
- `backend/app/services/knowledge_service.py` — `source_ref` on ingest; `delete_source_vectors`, `delete_knowledge_source`, `_delete_vectors_sync`
- `backend/app/api/v1/knowledge.py` — `DELETE /sources/{source_id}` (ownership 404/403 → 204)
- `backend/tests/test_knowledge_sources.py` — 5 delete tests

**Backend — created:**
- `backend/alembic/versions/a7b8c9d0e1f2_add_source_ref_to_knowledge_sources.py` — Alembic migration adding `source_ref` (revises `f5a6b7c8d9e0`)

**Frontend — modified:**
- `frontend/src/lib/hooks/useKnowledge.ts` — `useDeleteKnowledgeSource`
- `frontend/src/components/knowledge/KnowledgeBasePanel.tsx` — per-row delete button opening a `ConfirmModal` (pending state)
- `frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx` — delete hook mock + modal delete/cancel tests

**Frontend — created:**
- `frontend/src/components/ui/confirm-modal.tsx` — reusable accessible `ConfirmModal` (Esc/backdrop/close, focus, scroll-lock, destructive variant)
- `frontend/src/components/ui/__tests__/confirm-modal.test.tsx` — 5 tests

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-05 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 5 ACs implemented; ownership (403/404), best-effort vector deletion, and the `source_ref` join hold. Findings centered on the riskiest code (the Pinecone dual-strategy delete) having no real coverage.

### Action Items — all resolved

- [x] **[M1][Med]** `_delete_vectors_sync` (prefix-list+delete, and the metadata-filter fallback) had **zero** test coverage: the route test patched `delete_source_vectors`, the "swallows errors" test patched `asyncio.to_thread`, and the no-op test returned early — so the actual deletion logic never ran. Fixed: added `test_delete_vectors_sync_lists_prefix_and_deletes_by_id` (asserts the exact prefix `document_ref-1_chunk_`, namespace, and id-batched delete; fallback NOT used) and `test_delete_vectors_sync_falls_back_to_filter_delete` (list raises → `delete(filter={source, source_id})`). `[test_knowledge_sources.py]`
- [x] **[L1][Low]** Deleting a legacy `source_ref=NULL` row returned 204 while its vectors silently persisted. Fixed: `delete_source_vectors` now logs a WARNING distinguishing "no source_ref — vectors may persist" from "Pinecone unconfigured". Added `test_delete_source_vectors_warns_on_missing_ref`. `[knowledge_service.py]`
- [x] **[L2][Low]** A non-UUID `{source_id}` hit the DB driver and would 500. Fixed: the route validates `uuid.UUID(source_id)` up-front → 404 for a malformed id (before any query). Added `test_delete_source_400_for_malformed_id` (asserts 404 and that `db.execute` is never awaited). `[knowledge.py]`
- [ ] **[L3][Low]** Model/DB drift (ORM `source_ref` needs the manual `003` migration applied). No code change — inherent to the repo's manual-migration convention; documented in Dev Notes.

**Post-fix validation:** backend **259 pass** (+4 review-fix tests), new code ruff-clean.

## Change Log

- 2026-07-05: Implemented Story 4.7 — Delete Ingested Knowledge Sources. Added `DELETE /api/v1/knowledge/sources/{id}` removing the source's Pinecone vectors (new `source_ref` id-prefix, dual serverless/pod delete strategy, best-effort) and the `knowledge_sources` row, ownership-checked (404/403 → 204). Frontend per-row delete button (confirm + pending) via `useDeleteKnowledgeSource`. Alembic migration `a7b8c9d0e1f2` (add `source_ref`). Backend 255 tests pass (+5); frontend +2.
- 2026-07-05: Code review (adversarial) — Approve. Resolved 1 Medium + 2 Low: added real coverage for the dual-strategy vector delete (M1), a warning when a legacy source has no `source_ref` so deletion isn't silently partial (L1), and a malformed-id → 404 guard (L2). Backend 259 pass.
- 2026-07-05: UX — replaced the native `window.confirm` on delete with a reusable, accessible `ConfirmModal` (`components/ui/confirm-modal.tsx`: Esc/backdrop/close, focus, scroll-lock, destructive variant). `KnowledgeBasePanel` opens it per row; +5 modal tests, panel delete tests updated. Frontend suite +5 (94 pass).
- 2026-07-05: Migration correction — the `source_ref` column belongs in Alembic (the repo's schema-migration tool), not `supabase/migrations/*.sql` (which is RLS/storage-policy only). Replaced the raw SQL file with Alembic revision `a7b8c9d0e1f2` (revises `f5a6b7c8d9e0`); reconciled the running DB (column applied out-of-band) via `alembic stamp a7b8c9d0e1f2`. Apply elsewhere with `uv run alembic upgrade head`.
