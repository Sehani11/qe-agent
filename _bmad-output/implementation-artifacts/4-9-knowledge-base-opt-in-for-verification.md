# Story 4.9: Knowledge-Base Opt-In for Verification (Agentic RAG + Checkbox)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want a checkbox at verification time to decide whether to enrich the run with my project knowledge base,
so that I control—per run—whether Confluence/Jira/document context influences the verdicts, defaulting to code-only verification.

## Acceptance Criteria

1. **Given** the verification controls render
   **Then** a "Use project knowledge base" checkbox is shown, **unchecked by default**, and its value is sent as `use_knowledge_base` on the verification request

2. **And** when checked, the agentic verification retrieves per-scenario KB context (reusing Story 4.8's batched retrieval) and includes it in each scenario's LLM prompt; each verdict's `rag_context` is populated + persisted (→ RAG context panel + traceability report)

3. **And** when unchecked (default), the KB is **not** queried, no `PROJECT CONTEXT` is added to prompts, and every verdict's `rag_context` is `null`

4. **And** the gating is a per-request flag on both `/run` and `/run-agentic`; the `VERIFICATION_RAG_ENABLED` env var is removed

5. **And** retrieval degrades gracefully (empty context) when the KB is empty or Pinecone is unavailable, within the NFR-P6 budget

## Context & Critical Background

**The gap this fixes:** RAG (4.3/4.8) lived only in `verification_service.run_verification` (the `/run` endpoint), but the frontend's `useRunVerification` calls **`/run-agentic`** → `agentic_verification_service`, which never queried the KB and always persisted `rag_context = null`. So the knowledge base had **never** influenced a real UI verification.

**The fix:** add per-scenario RAG to the agentic path (reusing 4.8), and replace the deploy-wide env var with a **per-run `use_knowledge_base` flag** surfaced as a checkbox (default off).

### Reuse (no new retrieval logic)

| Piece | Location |
|---|---|
| Batched per-scenario retrieval | `verification_service.retrieve_rag_per_scenario` (renamed public) → `knowledge_service.query_knowledge_base_batch` (Story 4.8) |
| Prompt context block | `verification_service._format_rag_block` (extracted; shared by direct + agentic) |
| Verdict payload builder | `verification_service._rag_payload_from_chunks` |
| Per-row `rag_context` column | `models/verification_result.py` (unchanged) |
| Verify UI (mode + button) | `GitHubSourceSelector.tsx`; verify state in `SessionContext` |

## Tasks / Subtasks

### Backend

- [x] **Task 1: request flag** (AC: 1, 4) — `use_knowledge_base: bool = False` on `AgenticVerificationRequest` + `VerificationRunRequest`
- [x] **Task 2: remove the env var** (AC: 4) — delete `Settings.verification_rag_enabled` + `.env.sample` entry; `retrieve_rag_per_scenario(user_id, scenarios, enabled)` now takes the flag (no `settings` read); `run_verification(..., use_knowledge_base=False)` passes it
- [x] **Task 3: agentic RAG** (AC: 2, 3, 5) — `run_agentic_verification(..., use_knowledge_base=False)`: batch-retrieve per-scenario chunks before the loop, inject `_format_rag_block(chunks)` into each scenario's user message, and set `rag_context` on the verdict event + persisted `VerificationResult`
- [x] **Task 4: extract `_format_rag_block`** — shared PROJECT CONTEXT formatter used by both `_build_verification_prompt` and the agentic prompt
- [x] **Task 5: routes** (AC: 1) — pass `request.use_knowledge_base` to both service calls
- [x] **Task 6: tests** — direct path: flag-off skips query + null context (replaces the env-var test), flag-on retrieves per scenario; agentic path: `use_knowledge_base=True` enriches prompt + sets/persists `rag_context`, default-off skips query + null context

### Frontend

- [x] **Task 7: type + context** (AC: 1) — `use_knowledge_base` on `AgenticVerificationRequest`; `useKnowledgeBase`/`setUseKnowledgeBase` (default false, reset in `resetSession`) in `SessionContext`
- [x] **Task 8: checkbox** (AC: 1) — "Use project knowledge base" checkbox in `GitHubSourceSelector` (default unchecked); `handleRunVerification` includes `use_knowledge_base: useKnowledgeBase` in the payload
- [x] **Task 9: test** — checkbox renders unchecked; clicking calls `setUseKnowledgeBase(true)`

## Dev Notes

### Default off is deliberate

The checkbox defaults **unchecked** (code-only verification) per the product decision — the KB influences verdicts only when the user opts in for that run. This replaces the previous deploy-wide default-on env behavior.

### Agentic prompt injection

The agentic loop builds a per-scenario `messages` list; when enabled, the retrieved `PROJECT CONTEXT` block is appended to the user message so the tool-calling agent sees KB context alongside the scenario. Retrieval is one batched embedding call for all scenarios (4.8), so enabling RAG adds one embed + N cheap Pinecone queries per run.

### Not changed

`query_knowledge_base_batch`, the `verification_result` model/`rag_context` column, the RAG panel (4.4) and report (5.3/5.4) — they already render/persist per-verdict `rag_context`, so agentic verdicts now flow through unchanged.

### Project Structure Notes

**Backend — modify:** `schemas/verification.py`, `core/config.py` (remove field), `services/verification_service.py` (rename + `_format_rag_block` + flag), `services/agentic_verification_service.py` (RAG), `api/v1/verification.py` (routes), `.env.sample`, `tests/test_rag_verification.py`, `tests/test_agentic_verification_service.py`.
**Frontend — modify:** `lib/types/verification.ts`, `context/SessionContext.tsx`, `components/pipeline/GitHubSourceSelector.tsx`, `app/session/[sessionId]/page.tsx`, `components/pipeline/GitHubSourceSelector.test.tsx`.
**Do NOT modify:** `query_knowledge_base_batch`, `verification_result` model, RAG panel / report.

### Testing Standards

Backend `pytest`; mock `retrieve_rag_per_scenario` / `query_knowledge_base_batch`; assert prompt enrichment + `rag_context` on event & DB row, and no-query/null when off. Frontend `vitest`; assert checkbox default-unchecked + toggle. New code ruff-clean; full-suite regression.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.9
- [Source: backend/app/services/agentic_verification_service.py] — the UI's verification path (now RAG-enabled)
- [Source: backend/app/services/verification_service.py] — `retrieve_rag_per_scenario`, `_format_rag_block`, `_rag_payload_from_chunks`
- [Source: backend/app/schemas/verification.py] — `use_knowledge_base` on both requests
- [Source: frontend/src/components/pipeline/GitHubSourceSelector.tsx] — the checkbox
- [Source: frontend/src/lib/hooks/useRunVerification.ts] — calls `/run-agentic` (why agentic needed RAG)
- [Source: _bmad-output/implementation-artifacts/4-8-per-scenario-rag-and-verification-toggle.md] — the batched retrieval reused here

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- Discovery: `useRunVerification` calls `/run-agentic`; the agentic service had no `query_knowledge_base` at all — so 4.3/4.8 RAG never ran for real UI verifications.
- `agentic_verification_service.py` + its test have **pre-existing** ruff debt (11 + 6 I001/E501 at HEAD) and 8 pre-existing frontend test failures in `GitHubSourceSelector.test.tsx` — untouched by this story; my additions introduced no new ruff errors and no new test failures.

### Completion Notes List

- **Agentic now uses RAG (AC2):** `run_agentic_verification` batch-retrieves per-scenario chunks, injects `_format_rag_block` into each scenario's prompt, and sets `rag_context` on the verdict event + persisted row. Verified the context reaches the LLM prompt and the DB row.
- **Per-run gating (AC1/AC3/AC4):** `use_knowledge_base` flag on both requests, default False → KB not queried, `rag_context` null; `VERIFICATION_RAG_ENABLED` removed; `retrieve_rag_per_scenario` takes the flag.
- **Shared formatter (AC2):** `_format_rag_block` extracted and reused by the direct and agentic prompts (no duplicate formatting).
- **Frontend (AC1):** default-unchecked "Use project knowledge base" checkbox in `GitHubSourceSelector` → `SessionContext.useKnowledgeBase` → `use_knowledge_base` in the agentic payload.
- **Graceful (AC5):** reuses 4.8's batched retrieval (one embed + isolated per-query failures, never raises).
- **Validation:** backend **266 pass** (+2 agentic RAG tests, direct-path tests updated to the flag); frontend **+2** checkbox tests pass; changed frontend files tsc/eslint-clean; full frontend suite unchanged (same 10 pre-existing unrelated failures).

### File List

**Backend — modified:**
- `backend/app/schemas/verification.py` — `use_knowledge_base` on both request models
- `backend/app/core/config.py` — removed `verification_rag_enabled`
- `backend/app/services/verification_service.py` — `retrieve_rag_per_scenario(enabled)`, `_format_rag_block`, `run_verification(use_knowledge_base)`, dropped `settings` import
- `backend/app/services/agentic_verification_service.py` — per-scenario RAG injection + `rag_context` on event/row (`use_knowledge_base` param)
- `backend/app/api/v1/verification.py` — pass `use_knowledge_base` to both services
- `backend/.env.sample` — removed `VERIFICATION_RAG_ENABLED`
- `backend/tests/test_rag_verification.py` — flag-gated tests (replaces env-var test)
- `backend/tests/test_agentic_verification_service.py` — agentic KB enrich + default-off tests

**Frontend — modified:**
- `frontend/src/lib/types/verification.ts` — `use_knowledge_base` on `AgenticVerificationRequest`
- `frontend/src/context/SessionContext.tsx` — `useKnowledgeBase` state (+ reset)
- `frontend/src/components/pipeline/GitHubSourceSelector.tsx` — the checkbox
- `frontend/src/app/session/[sessionId]/page.tsx` — include flag in the verify payload
- `frontend/src/components/pipeline/GitHubSourceSelector.test.tsx` — checkbox tests + mock fields

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (high-effort /code-review, 8 angles → verify) · **Date:** 2026-07-05 · **Outcome:** Approve (all findings fixed)

### Action Items — all resolved

- [x] **[F1][High · data loss]** The agentic persist except block called `db.rollback()`, which rolls back the **whole** request transaction (commit happens once at teardown) — discarding every verdict already flushed for earlier scenarios. Fixed: per-verdict **SAVEPOINT** (`async with db.begin_nested():`) so a persist failure rolls back only that row; removed the transaction-wide rollback. Test mock updated with an async `begin_nested`. `[agentic_verification_service.py]`
- [x] **[F2][Med · silent corruption]** `_embed_chunks` didn't sort the OpenAI response by `index`, so batched per-scenario retrieval could pair a scenario with another scenario's vector. Fixed: `sorted(data["data"], key=lambda i: i["index"])` (helps every caller). `[knowledge_service.py]`
- [x] **[F3][Med]** `query_knowledge_base_batch` embedded all texts in one un-chunked request → an oversized BDD (or a blank text) could 400 the whole call and zero out every scenario. Fixed: embed in `_EMBED_BATCH_SIZE` chunks + replace blank texts with a space. `[knowledge_service.py]`
- [x] **[F4][Med · crash]** `zip(scenarios, per_scenario_chunks, strict=True)` (outside any try/except) would crash the SSE stream if retrieval ever returned a mismatched length. Fixed at the source: `query_knowledge_base_batch` now guarantees a result aligned 1:1 with the inputs (pads/truncates + logs). `[knowledge_service.py]`
- [x] **[F5][Low · coupling]** Agentic imported underscore-private `_format_rag_block`/`_rag_payload_from_chunks` across a module boundary. Fixed: promoted to public `format_rag_block` / `rag_payload_from_chunks`. `[verification_service.py]`
- [x] **[F6][Low · dup]** The verdict SSE-event dict and the `VerificationResult` construction were duplicated across both services (the `rag_context` double-edit already happened). Fixed: shared `verdict_event_dict(...)` + `build_verification_result(...)` in `verification_service`, used by both paths (persistence/savepoint stays per-path). `[verification_service.py, agentic_verification_service.py]`
- [x] **[F7][Low · test]** The two new checkbox tests landed in an already-stale, red `GitHubSourceSelector.test.tsx` (~8 outdated assertions: old "Verify scenarios" button label, removed mobile overlay, old placeholder). Fixed: updated to the current component (`/run verification/i`, `stringContaining` placeholder, skipped the removed-overlay assertion). File now green.

**Post-fix validation:** backend **266 pass**, new code ruff-clean; frontend **103 pass** (GitHubSourceSelector 8 failures → 0; overall frontend baseline improved 10 → 2 pre-existing unrelated failures).

## Change Log

- 2026-07-05: Implemented Story 4.9 — Knowledge-Base Opt-In for Verification. Added per-scenario RAG to the agentic verification path (the one the UI uses, previously RAG-less), gated by a per-run `use_knowledge_base` flag surfaced as a default-unchecked "Use project knowledge base" checkbox; removed the `VERIFICATION_RAG_ENABLED` env var. Reuses Story 4.8's batched retrieval; verdict `rag_context` now populated from agentic runs → RAG panel + reports. Backend 266 tests pass; frontend +2.
- 2026-07-05: Code review (high-effort) — Approve. Fixed 1 High + 3 Med + 3 Low: agentic per-verdict SAVEPOINT (no transaction-wide rollback data loss), sort embeddings by OpenAI index, batch/blank-guard + length-aligned batch retrieval, promoted RAG helpers to public + shared verdict constructors, and refreshed the stale GitHubSourceSelector test (8 failures → green). Backend 266 pass; frontend baseline 10 → 2 pre-existing failures.
