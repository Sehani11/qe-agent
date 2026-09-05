# Story 4.8: Per-Scenario RAG Retrieval & Verification Toggle

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want each BDD scenario verified against the knowledge-base context most relevant to *that* scenario, and the ability to turn knowledge-base enrichment off,
so that verdicts are more accurate, and I can verify against the ticket/code alone when I don't want the knowledge base to influence results.

## Acceptance Criteria

1. **Given** verification runs for a session with multiple BDD scenarios and a populated knowledge base
   **When** each scenario's prompt is assembled
   **Then** the service retrieves top-K relevant chunks **per scenario** (querying with that scenario's own Gherkin/AC `text`), runs the retrievals **in parallel**, and includes each scenario's own chunks in its LLM prompt (FR35, FR41)

2. **And** each verdict's persisted `rag_context` reflects the chunks retrieved for **that** scenario (no longer one shared payload) — surfaced per-row in the RAG context panel and the traceability report

3. **And** retrieval stays within the NFR-P6 budget (parallelized) and degrades gracefully to no-enrichment when the KB is empty or Pinecone is unavailable (`query_knowledge_base` already returns `[]` on any failure)

4. **And given** `VERIFICATION_RAG_ENABLED=false`
   **When** verification runs
   **Then** the KB is **not** queried at all, no `PROJECT CONTEXT` is added to prompts, and every verdict's `rag_context` is `null`

5. **And** the toggle defaults to `true` when unset

## Context & Critical Background

Refines Story 4.3's RAG-enriched verification. Two changes, both in `verification_service.run_verification`:

- **Before:** one `query_knowledge_base(user_id, bdd_content)` for the whole run; the *same* chunks fed every scenario's prompt and every verdict's `rag_context`.
- **After:** one retrieval **per scenario** keyed on `scenario["text"]` (which already includes the `# Source AC:` clause + Given/When/Then), executed with `asyncio.gather`; each scenario's prompt and verdict carry their **own** context.

Why this is more accurate: a scenario about "failed login" now retrieves KB context about error handling, not a blur of chunks pulled from the concatenated BDD. `verification_results.rag_context` is already a **per-row** JSONB column, so per-scenario context flows through to the panel (4.4) and report (5.3/5.4) with **no schema change**.

### Reuse / touch points

| Piece | Location |
|---|---|
| Scenario struct `{id, title, text}` | `verification_service.parse_bdd_scenarios` |
| Per-scenario prompt (already takes `rag_chunks`) | `verification_service._build_verification_prompt` |
| KB retrieval (graceful, 10s-bounded) | `knowledge_service.query_knowledge_base` |
| Per-row `rag_context` persistence | `models/verification_result.py` (unchanged) |
| Config pattern (`BaseSettings`) | `core/config.py` |

## Tasks / Subtasks

- [x] **Task 1: config toggle** (AC: 4, 5) — `Settings.verification_rag_enabled: bool = True` (env `VERIFICATION_RAG_ENABLED`); documented in `.env.sample`
- [x] **Task 2: per-scenario parallel retrieval** (AC: 1, 3) — `_retrieve_rag_per_scenario(user_id, scenarios)` → `asyncio.gather` of `query_knowledge_base(user_id, s["text"])`, aligned to scenarios; returns all-empty (no query) when the toggle is off
- [x] **Task 3: per-scenario context in the loop** (AC: 1, 2) — `zip(scenarios, per_scenario_chunks, strict=True)`; `_rag_payload_from_chunks(chunks)` builds each verdict's own `rag_context` (SSE event + DB row)
- [x] **Task 4: tests** (AC: 1–5)
  - [x] `test_rag_context_retrieved_per_scenario` — `query_knowledge_base` called **once per scenario**, each keyed on that scenario's text; both verdicts carry rag_context (replaces the old "shared/called-once" test)
  - [x] `test_rag_disabled_by_env_skips_retrieval` — toggle off → KB never queried, verdict + DB `rag_context` is `null`
  - [x] existing single-scenario RAG tests still pass (called once = one scenario)

## Dev Notes

### Latency (NFR-P6)

Retrievals run concurrently via `asyncio.gather`, so wall-clock ≈ the slowest single query (each internally bounded to 10s by `query_knowledge_base`), not the sum. For the NFR-P4 cap of 15 scenarios that's 15 concurrent embed+query pairs — acceptable. (A future optimization could share one Pinecone client / batch embeddings; not needed now.)

### Toggle semantics

`verification_rag_enabled=false` short-circuits **before** any query (`_retrieve_rag_per_scenario` returns `[[] for _ in scenarios]`), so there's zero KB/Pinecone/OpenAI-embed cost and no `PROJECT CONTEXT` in prompts — a clean "ticket/code only" mode. Default `true` preserves Story 4.3 behavior.

### Not changed

No API/route/schema/migration changes; `rag_context` was already per-row. `query_knowledge_base` unchanged (still `{user_id}:knowledge`, top_k=5, graceful). Frontend unchanged — the RAG panel/report already render per-verdict `rag_context`.

### Project Structure Notes

**Backend — modify:** `app/core/config.py` (toggle), `app/services/verification_service.py` (`_retrieve_rag_per_scenario`, `_rag_payload_from_chunks`, per-scenario loop), `tests/test_rag_verification.py`, `.env.sample`.
**Do NOT modify:** `knowledge_service.query_knowledge_base`, `verification_result` model, routes, frontend.

### Testing Standards

`pytest`/`pytest-asyncio`; mock `_ks.query_knowledge_base` + `settings.verification_rag_enabled`; assert per-scenario call count and null-context when disabled. Full-suite regression; new backend code ruff-clean.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.8
- [Source: backend/app/services/verification_service.py:203] — `run_verification` (per-scenario retrieval + toggle)
- [Source: backend/app/services/knowledge_service.py:146] — `query_knowledge_base` (unchanged, graceful, 10s-bounded)
- [Source: backend/app/core/config.py] — `verification_rag_enabled`
- [Source: _bmad-output/implementation-artifacts/4-3-rag-enriched-verification-integration.md] — the story this refines

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New backend code ruff-clean. (Two pre-existing E501s in `config.py` lines 22/36 — `direct_database_url`/`fine_tuned_model_api_key` comments — are untouched by this story.)
- Replaced the old `test_rag_context_shared_across_multiple_scenarios` (asserted a single shared query) with `test_rag_context_retrieved_per_scenario` — the contract deliberately changed.

### Completion Notes List

- **Accuracy (AC1/AC2):** retrieval is now per-scenario (`_retrieve_rag_per_scenario`, parallel `asyncio.gather` keyed on `scenario["text"]`), and each verdict/DB row carries its own `rag_context` via `_rag_payload_from_chunks`. Verified `query_knowledge_base` is called once per scenario with the right text.
- **Toggle (AC4/AC5):** `VERIFICATION_RAG_ENABLED` (default true); when false, `_retrieve_rag_per_scenario` returns all-empty without querying → no `PROJECT CONTEXT`, `rag_context=null` on event + DB row. Verified `query_knowledge_base` not called.
- **Graceful (AC3):** unchanged `query_knowledge_base` still returns `[]` on empty/failure; parallel retrieval keeps within NFR-P6.
- **No schema/route/frontend change** — `rag_context` was already a per-row column; the panel/report render per-verdict context automatically.
- **Validation:** backend **260 pass**; new code ruff-clean; `.env.sample` documents the toggle.

### File List

**Backend — modified:**
- `backend/app/core/config.py` — `verification_rag_enabled` setting
- `backend/app/services/knowledge_service.py` — `query_knowledge_base_batch` (one embeddings request for all scenarios, shared client, per-query failure isolation)
- `backend/app/services/verification_service.py` — `_retrieve_rag_per_scenario` (uses the batch fn), `_rag_payload_from_chunks`, per-scenario loop (replaces single shared retrieval)
- `backend/tests/test_rag_verification.py` — per-scenario test + toggle-off test + `TestQueryKnowledgeBaseBatch` (batch/isolation/graceful)
- `backend/.env.sample` — `VERIFICATION_RAG_ENABLED` documented

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (high-effort /code-review, 8 angles → verify) · **Date:** 2026-07-05 · **Outcome:** Approve (all findings fixed)

Two independent finder passes converged on the fan-out cost of the first-cut per-scenario retrieval.

### Action Items — all resolved

- [x] **[F1][Efficiency+silent-regression]** The first cut `asyncio.gather`'d one `query_knowledge_base` **per scenario**, so each scenario ran its **own** OpenAI embedding request + fresh Pinecone client (~N× cost at the 15-scenario cap); a concurrent 429 burst was swallowed as `[]`, silently verifying scenarios without the RAG context they'd have had (verdict could flip, no signal). Fixed: added `knowledge_service.query_knowledge_base_batch(user_id, texts)` — **one** batched `_embed_chunks` call for all scenario texts + **one** shared Pinecone client, then a query per text. `_retrieve_rag_per_scenario` now delegates to it. Embedding calls N→1, client inits N→1; per-scenario relevance preserved.
- [x] **[F2][Robustness]** `gather` ran without `return_exceptions=True`, coupling the whole run to `query_knowledge_base` never raising. Fixed inside the batch fn: the per-query `gather` uses `return_exceptions=True` and coerces any exception to `[]`, so one throttled/failed query is isolated to its own scenario instead of aborting the run. Covered by `test_isolates_a_single_failed_query`.
- [x] **[F3][Simplification]** Dropped the dead `[r or [] for r in results]` coercion (the batch fn returns properly-aligned lists).

**Post-fix validation:** backend **264 pass** (+4 batch tests: batches-once, no-pinecone, per-query isolation, empty-input); new code ruff-clean.

## Change Log

- 2026-07-05: Implemented Story 4.8 — Per-Scenario RAG Retrieval & Verification Toggle. Verification now retrieves KB context per scenario (parallel `asyncio.gather` on each scenario's text) instead of one shared query, so each verdict's `rag_context` is scenario-specific (more accurate; no schema change — the column is already per-row). Added `VERIFICATION_RAG_ENABLED` env toggle (default true) to disable KB enrichment entirely (ticket/code-only verification). Backend 260 tests pass; new code ruff-clean.
- 2026-07-05: Code review (high-effort) — Approve. Resolved 1 Efficiency + 1 Robustness + 1 Simplification: replaced the per-scenario embedding fan-out with `query_knowledge_base_batch` (one batched embed + shared client, per-query failure isolation via `return_exceptions=True`), dropped a dead coercion. Backend 264 pass.
