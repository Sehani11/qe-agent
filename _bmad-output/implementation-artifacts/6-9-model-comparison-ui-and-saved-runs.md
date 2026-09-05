# Story 6.9: Model Comparison UI & Saved Evaluation Runs

Status: done

<!-- Retro-documented 2026-08-22. This story was implemented outside the BMAD
     workflow on 2026-08-16/17; the acceptance criteria below were reconstructed
     from the shipped code and commit history, not drafted before the work.
     See the Change Log. -->

## Story

As a **researcher**,
I want to run and review fine-tuned vs. general-LLM comparisons from inside the app,
so that the evaluation is reproducible and inspectable by someone who does not run the CLI — and so a run can be re-read after the terminal session that produced it is gone.

## Acceptance Criteria

1. **Given** an authenticated user supplies either a Jira ticket ID or pasted acceptance criteria
   **When** `POST /api/v1/evaluation/compare` is called
   **Then** both providers generate for that input and each result is scored with **the same `evaluation_metrics` functions the batch CLI uses** — the route handles auth, input resolution and HTTP mapping only, so the API and `app.evaluate_models` can never drift into measuring different things

2. **And** every result carries `configured_provider`, `effective_provider`, `fallback_reason` and `model_identifier`, so a silently degraded generation is **visible in the UI** rather than merely counted — the fallback returns an identically shaped response, and attribution is the only thing that distinguishes them

3. **And** the comparison route calls `generate_one(..., allow_fallback=False)` explicitly: this caller is **measuring**, so a fine-tuned failure must be a failed row, not general-LLM output wearing the fine-tuned label. The explicit argument outranks `FINE_TUNED_ALLOW_FALLBACK`, so no configuration change can silently re-enable the fallback underneath a measuring caller

4. **And** `POST /evaluation/runs` runs a batch over several Jira tickets under a caller-supplied `run_id`, persisting rows through the existing `persist_outcome`, and returns a **per-item outcome list** — one unresolvable ticket must not discard the rest of the batch

5. **And** `GET /evaluation/runs`, `GET /evaluation/report` and `DELETE /evaluation/runs/{run_id}` list, summarise and delete saved runs, every one of them scoped to `user_id` in the query itself so a guessed `run_id` cannot reach another user's rows

6. **And** the report excludes degraded rows from every average and **counts them separately** — averaging in a row the fine-tuned model did not produce is precisely how this comparison becomes wrong while continuing to look healthy. Omitting `run_id` pools every saved run into a combined report

7. **And** the general LLM runs **before** the fine-tuned model, because the baseline answers in a few seconds and the fine-tune can take a minute — running it first means the slow column is the only thing still pending in the UI

8. **And given** the user opens `/comparison`, `ModelComparisonPanel` runs a single comparison side by side and `SavedRunsPanel` lists saved runs, renders the report for one run or all runs combined, and deletes a run

## Context & Critical Background

### Why an API at all, when 6.3 shipped a CLI

Story 6.3 built `python -m app.evaluate_models` and `docs/evaluation-report.md`. Both are correct and remain the batch path. What they do not give you is a way to ask "what does this model do with *this* ticket" without a terminal, a configured environment and a local Ollama — which meant every spot check went through the person who had all three. This story exposes the same machinery over HTTP.

The risk in doing that is **two implementations of the same measurement**. It is avoided structurally rather than by discipline: the route imports `generate_one`, `persist_outcome`, `coverage`, `duplicate_rate`, `summarise_group` and `score_row` from the evaluation services and adds no scoring logic of its own.

### The instrumentation this depends on

`FineTunedModelProvider` falls back to the general LLM on timeout or error and returns a byte-identical response shape. Story 6.1 built `effective_provider` for exactly this reason, and Story 6.3 made it a persisted column. This story is the first thing that puts it **on a screen**, which is also what makes AC3's `allow_fallback=False` necessary — a UI that renders a fallback row as a fine-tuned result would be worse than no UI, because it would look authoritative.

### Reuse map

| Piece | Location |
|---|---|
| One generation, attributed | `evaluation_service.generate_one(item, provider, allow_fallback=)` |
| Row persistence (upsert) | `evaluation_service.persist_outcome` |
| Objective metrics | `evaluation_metrics.coverage / duplicate_rate / summarise_group` |
| Row scoring | `evaluation_report.score_row` |
| AC clause splitting | `evaluation_set.ac_clauses`, `EvalItem` |
| Ticket resolution | `jira_service` |
| Results table + RLS | `models/evaluation_result.py` (migration `e1f2a3b4c5d6`, Story 6.3) |

## Tasks / Subtasks

### Backend

- [x] **Task 1: `schemas/evaluation.py`** (AC: 1–6) — `CompareRequest/Response`, `ProviderResult`, `BatchCompareRequest/Response`, `BatchItemOutcome`, `RunSummary`, `RunReport`, `ProviderMetrics`, `GroupMetrics`
- [x] **Task 2: `POST /evaluation/compare`** (AC: 1–3, 7) — resolve input (ticket or pasted AC), run `PROVIDERS = ("general_llm", "fine_tuned")` in that order, score each via the shared metrics, return `comparable` + `notes`
- [x] **Task 3: `POST /evaluation/runs`** (AC: 4) — batch over ticket IDs under one `run_id`, per-item outcome, persisted via `persist_outcome`
- [x] **Task 4: `GET /evaluation/runs`** (AC: 5) — grouped summary per run, newest first, degraded rows counted in SQL
- [x] **Task 5: `GET /evaluation/report`** (AC: 6) — per-run or pooled; degraded rows excluded from averages and reported separately
- [x] **Task 6: `DELETE /evaluation/runs/{run_id}`** (AC: 5) — user-scoped delete, 404 when `rowcount == 0`
- [x] **Task 7: `FINE_TUNED_ALLOW_FALLBACK` + `allow_fallback` plumbing** (AC: 3) — settings default (True, serving), `get_bdd_model_provider(allow_fallback=)`, threaded through `generate_one`
- [x] **Task 8: `tests/test_evaluation_compare_api.py` (10), `tests/test_evaluation_runs_api.py` (9)**

### Frontend

- [x] **Task 9: `lib/types/evaluation.ts`** — request/response types mirroring the schemas
- [x] **Task 10: `lib/hooks/useModelComparison.ts`** — compare mutation, runs/report queries, delete mutation
- [x] **Task 11: `components/evaluation/ModelComparisonPanel.tsx`** (AC: 8) — ticket ID *or* pasted AC, side-by-side result columns, answered/did-not-answer state, "Answered by …" when `effective_provider` differs
- [x] **Task 12: `components/evaluation/SavedRunsPanel.tsx`** (AC: 8) — run list, "All runs combined" selector, report render, delete
- [x] **Task 13: `/comparison` page** — mounts both panels

## Dev Notes

### Provider order is a UX decision with a measurement consequence

`PROVIDERS = ("general_llm", "fine_tuned")` is commented in the source as ordered for the UI. It is worth keeping that comment: reordering it would not change any number, but it would leave the fast column pending behind the slow one, and someone would eventually "fix" the latency by adding a timeout that reintroduces exactly the fallback AC3 exists to prevent.

### Degraded rows

Two different things are done with them, deliberately:

- `GET /runs` **counts** them (`configured_provider != effective_provider`) so a run can be judged before its report is opened.
- `GET /report` **excludes** them from averages and reports the exclusion count.

Neither hides them. Story 6.3's `assert_run_is_trustworthy` still governs the batch CLI path.

### What this story does not do

No new metric, no new judge, no change to `docs/evaluation-report.md`, and no change to the serving decision — `general_llm` remains the production default and the fine-tune remains local-only for research. This is an interface over Story 6.3's machinery.

### Project Structure Notes

**Backend — created:** `app/api/v1/evaluation.py`, `app/schemas/evaluation.py`, `tests/test_evaluation_compare_api.py`, `tests/test_evaluation_runs_api.py`.
**Backend — modified:** `app/api/v1/api.py` (router mount), `app/core/config.py` (`fine_tuned_allow_fallback`), `app/services/bdd_model/factory.py`, `app/services/bdd_model/fine_tuned_provider.py`, `app/services/evaluation_service.py`.
**Frontend — created:** `src/app/comparison/page.tsx`, `src/components/evaluation/ModelComparisonPanel.tsx`, `src/components/evaluation/SavedRunsPanel.tsx`, `src/lib/hooks/useModelComparison.ts`, `src/lib/types/evaluation.ts`.
**Do NOT modify:** `evaluation_metrics`, `evaluation_judge`, `evaluate_models` — the API must consume them, never reimplement them.

### References

- [Source: _bmad-output/implementation-artifacts/6-3-model-evaluation-pipeline.md] — the runner, metrics, judge and results table this exposes
- [Source: _bmad-output/implementation-artifacts/6-1-bdd-model-provider-abstraction.md] — `effective_provider` attribution
- [Source: backend/app/services/evaluation_service.py:140] — `generate_one(..., allow_fallback=)`
- [Source: backend/app/core/config.py:55] — `fine_tuned_allow_fallback` and why its default is True

## Dev Agent Record

### Completion Notes List

- **No second implementation of the measurement (AC1).** The route computes nothing itself; it imports `generate_one`, `score_row`, `coverage`, `duplicate_rate` and `summarise_group`. A metric change lands in both paths at once.
- **Measuring caller is explicit (AC3).** `allow_fallback=False` is passed at the call site in `evaluation.py`, not inherited from settings.
- **Batch isolation (AC4).** A failed ticket yields a `BatchItemOutcome` with `saved: false` and an error string; the remaining tickets still run and persist.
- **Tenant scoping is in the query (AC5).** `list_runs`, `run_report` and `delete_run` all filter on `user_id` in SQL rather than checking after the fetch; `delete_run` 404s on `rowcount == 0`.
- **Degraded rows are counted, not averaged (AC6).** Report excludes them and states the exclusion; the run list surfaces the count up front.
- **Tests:** 19 new backend tests across the two API suites (10 compare, 9 runs).

### File List

**Backend — created:**
- `backend/app/api/v1/evaluation.py` — 5 routes
- `backend/app/schemas/evaluation.py` — request/response models
- `backend/tests/test_evaluation_compare_api.py` — 10 tests
- `backend/tests/test_evaluation_runs_api.py` — 9 tests

**Backend — modified:**
- `backend/app/api/v1/api.py` — mount `/evaluation`
- `backend/app/core/config.py` — `fine_tuned_allow_fallback`
- `backend/app/services/bdd_model/factory.py` — `get_bdd_model_provider(allow_fallback=)`
- `backend/app/services/bdd_model/fine_tuned_provider.py` — honour the flag
- `backend/app/services/evaluation_service.py` — thread `allow_fallback` through `generate_one`

**Frontend — created:**
- `frontend/src/app/comparison/page.tsx`
- `frontend/src/components/evaluation/ModelComparisonPanel.tsx`
- `frontend/src/components/evaluation/SavedRunsPanel.tsx`
- `frontend/src/lib/hooks/useModelComparison.ts`
- `frontend/src/lib/types/evaluation.ts`

## Change Log

- 2026-08-22: **Retro-documented.** The work landed on 2026-08-16/17 outside the BMAD workflow, so this file was written after the fact from the shipped code and commit history rather than drafted ahead of implementation. The acceptance criteria are a reconstruction of what the code actually satisfies; they were not agreed in advance, and this note exists so no one later reads them as if they were.
- 2026-08-17: **`FINE_TUNED_ALLOW_FALLBACK` added and threaded to the call site.** Serving keeps the fallback (a user waiting on scenarios should get scenarios); the comparison API passes `allow_fallback=False` explicitly, and because an explicit argument beats the setting, a config change cannot re-enable the fallback under a measuring caller.
- 2026-08-16: **Implemented — evaluation API and `/comparison` UI.** Five routes over Story 6.3's existing runner, metrics and results table: single comparison, batch run under a caller-supplied `run_id`, saved-run list, per-run or pooled report, and user-scoped delete. Every score comes from the shared `evaluation_metrics` functions, so the API and the batch CLI cannot measure different things. `effective_provider` / `fallback_reason` are surfaced in the UI, degraded rows are excluded from averages and counted separately, and the baseline runs first so the fine-tune's minute-long generation is the only thing left pending. 19 new backend tests.
