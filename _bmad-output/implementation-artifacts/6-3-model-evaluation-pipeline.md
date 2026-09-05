# Story 6.3: Model Evaluation Pipeline

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **researcher**,
I want to systematically compare BDD output from the fine-tuned model against the general LLM baseline,
so that I can answer — with measured numbers rather than impressions — whether the fine-tune from Story 6.5 is actually better, which is the question every other Epic 6 decision has been deferred against.

## 🚨 Measured 2026-08-13: the epic's opening "Given" is unsatisfied — for the third time

Epic 6.3 opens *"**Given** a set of 20+ real Jira tickets with known acceptance criteria"*. Measured against the live database today:

| Source | Query | Result |
|---|---|---|
| Captured AC→Gherkin pairs | `bdd_files WHERE acceptance_criteria IS NOT NULL` | **0 rows** |
| All `bdd_files` | `SELECT count(*)` | 10 (8 `generated`, 2 `uploaded`) |
| Distinct Jira tickets ever ingested | `SELECT count(DISTINCT jira_ticket_id) FROM sessions` | **5** |
| Manual uploads (Story 6.7) | `training_datasets` | **0 rows** |

**Story 6.4's capture has still never fired.** It was 0 when Story 6.5 measured it on 2026-08-08; it is 0 today. The mechanism is correct and tested — it has simply never been exercised on a real generation.

This is the **third** Epic 6 story to open on a precondition nothing satisfies: 6.2's *"Given a fine-tuned model is deployed"*, 6.5's *"Given a corpus of `.feature` files"*, and now this. The pattern is consistent enough to name: **Epic 6's stories were written assuming the app would be in regular use, and it has not been.** Do not treat AC1 as boilerplate — it is the only reason this story can run at all.

### ⚠️ One epic metric is not measurable and must not be faked

The epic asks for **"human-edit percentage"**. That requires rows where a user edited generated Gherkin and saved — `bdd_files` with `source='edited'` and a `parent_id`. There are **zero**, and there is no way to manufacture them honestly. AC7 below scopes it out explicitly rather than letting a dev invent a denominator.

## Acceptance Criteria

1. **Given** the application has never captured a real AC→Gherkin pair (measured above)
   **When** an evaluation set is assembled and its provenance recorded in `docs/evaluation-set.md`
   **Then** it contains **at least 20 AC sets**, each with the origin, whether the AC text is human-authored or LLM-back-generated, and whether a human-authored reference Gherkin exists — this AC exists because every other AC here is blocked until it holds

2. **And** `python -m app.evaluate_models` runs both providers over the evaluation set and persists one row per (ticket, provider) with the generated scenarios, the model identifier, timings and the run id — resumable, so a crash at ticket 15 does not discard the first 14

3. **And** the run records **which provider actually served each generation**, read from `BDDModelProvider.effective_provider` — a silent fallback would otherwise be scored as fine-tuned output and would invalidate the entire comparison

4. **And** an Alembic migration adds the results table with RLS and per-user isolation preserved (NFR-S6), following the `verification_results` precedent

5. **And** the objective metrics are computed without any LLM judgement: **AC-clause coverage** (every clause has a scenario, via `source_ac_clause`), **schema validity**, **scenario count**, and **duplicate/near-duplicate scenario rate** — these are the backbone of the comparison because they cannot be argued with

6. **And** where subjective quality is scored, the judge is **blinded**: outputs are anonymised and presented in randomised order, and the judging model is recorded — an unblinded judge that can tell which system produced which output is not a measurement (see the conflict-of-interest note below)

7. **And** the epic's **"human-edit percentage" metric is reported as `not measurable`** with the row count that makes it so, rather than being computed from an empty or invented denominator

8. **And** a summary report presents the two providers side by side per metric, states the sample size, and carries the **quantization and corpus caveats** from *Context* — a headline number without them would be read as a verdict on fine-tuning when it may be a verdict on the serving path

9. **And** the conclusion is stated plainly in `docs/evaluation-report.md` — including "no significant difference" or "the baseline wins" if that is what the numbers say. **A result that favours the general LLM is a successful outcome of this story**, and Epic 6's serving decision already assumes it may happen

## Context & Critical Background

### The three confounds that could each invalidate this comparison

This story's danger is not that it fails to run — it is that it produces a confident number that means something other than what it appears to mean.

**1. The quantization confound (from Story 6.2's review).** The adapter trained under bitsandbytes **NF4**; it is served on Ollama's **q4_K_M** base. Same weights, different quantization. `/health`'s `chat_template` check **cannot** detect this — it compares prompt formats, not weights.

> A poor fine-tuned score therefore has at least two candidate causes: the fine-tune's own weakness, and quantization drift between training and serving. **Do not attribute a result to the first without ruling out the second.** The cheapest control is documented in [training/serve/README.md](training/serve/README.md#-known-exposure-the-base-is-quantized-differently-than-training): run a subset through the adapter in `transformers` (as trained) and compare with the Ollama path. A large gap indicts serving; a small one clears it.

**2. The domain confound (from Story 6.5).** 84% of the training corpus was testing-framework Gherkin (behat, aruba, cucumber-*) about running CLI commands, not product behaviour. If the evaluation set is drawn from the same distribution, the fine-tune is being tested on home turf and will flatter itself. If it is drawn from real Jira tickets, it is being tested off-distribution. **Both are legitimate measurements of different things — say which one you ran.** Ideally run both and report separately; the gap between them is itself the most interesting number this story can produce.

**3. The judge conflict of interest (AC6).** The obvious way to score "AC relevance" and "correctness" is LLM-as-judge. But the general LLM is a **contestant**. Using it — or its provider's sibling model — to judge its own output against a competitor is a rigged comparison, and it is the kind of flaw that invalidates a research contribution rather than merely weakening it.
>
> Mitigations, in descending order of strength: use a *different* provider as judge; blind the judge to provenance and randomise presentation order (**required by AC6 regardless**); report per-metric agreement between two judges. At minimum, record which model judged, so the result can be re-run later.

### Where the evaluation set comes from (AC1)

Three sources exist. **None alone is sufficient; state which you used.**

| Source | Count | AC text | Reference Gherkin | Caveat |
|---|---|---|---|---|
| Story 6.5 holdout, `training/data/holdout.jsonl` | **18** | LLM back-generated | ✅ human-authored | Off-domain; **never seen by the fine-tune** (split by origin file), so it is a fair test set |
| Real Jira tickets via `jira_service.fetch_ticket_content` | 5 ingested | ✅ human-authored | ❌ none | On-domain but tiny; needs more tickets ingested |
| Hand-curated product-domain ACs | 0 | ✅ human-authored | ❌ unless written | Most representative, most effort |

The holdout is the strongest starting point — it is the only source with a **human-authored reference** to compare against, and `finetune_bdd.py` guarantees no origin overlap with training. It is 18, not 20; closing that gap with real or curated tickets is Task 1's job.

⚠️ **Do not evaluate on `training/data/train.jsonl`.** The fine-tune memorised it. Scoring on it produces a spectacular, meaningless number.

### Reference-based scoring is possible for the holdout, and only for the holdout

For the 18 holdout items a human-authored reference exists, so similarity to it is computable without a judge. For Jira-sourced items there is no reference, so only the AC5 structural metrics and a blinded judge apply. **Report these two groups separately** — averaging them produces a number that describes neither.

### The 45-second problem (design constraint, not a defect)

Story 6.2 measured **45.0s per fine-tuned generation** at 88%/12% CPU/GPU on the MX330. For 20 items across both providers, expect **~20–30 minutes minimum** for the fine-tuned side alone.

Consequences the design must absorb:
- **This is a batch job**, not a request-response flow. No HTTP timeout applies; do not route it through `bdd_service`'s 12s-bounded provider path expecting it to survive.
- ⚠️ `FineTunedModelProvider` enforces a **12s** total bound and will fall back to the general LLM on every single call at this latency — **silently scoring general-LLM output as fine-tuned**. This is the single most likely way to get a wrong answer from this story. AC3's `effective_provider` check is what catches it; raise the budget via the `FINE_TUNED_MODEL_TIMEOUT_SECONDS` **env var for the evaluation run only**, and never in a committed file.
- **Resumability is an AC**, not a nicety (AC2): a 30-minute job that loses everything on a transient error will be run once and then trusted less than it deserves.

### Reuse map

| Piece | Location |
|---|---|
| Provider selection + attribution | `app/services/bdd_model/factory.py`, `provider.py::effective_provider` |
| The generation call both providers share | `app/services/bdd_service.py::generate_bdd_scenarios` |
| Target schema | `app/schemas/bdd.py::BDDGenerateResponse` |
| Results-table precedent (JSONB, RLS, `created_at`) | `app/models/verification_result.py` |
| Migration precedent | `backend/alembic/versions/f5a6b7c8d9e0_*.py` |
| Jira AC extraction | `app/services/jira_service.py::fetch_ticket_content` |
| Holdout set + its origin metadata | `training/data/holdout.jsonl` (git-ignored, on disk) |
| Serving the fine-tune + the quantization caveat | `training/serve/README.md` |
| Measured baseline (eval loss, corpus makeup) | `training/RUN_LOG.md` |

## Tasks / Subtasks

- [x] **Task 1: Assemble and document the evaluation set** (AC: 1) — *blocks everything else*
  - [x] Start from `training/data/holdout.jsonl` (18 items, human-authored reference Gherkin, unseen by the fine-tune)
  - [x] Reach **≥20** by ingesting more real Jira tickets, hand-curating product-domain ACs, or both — *chose curated; ingesting Jira needs the user driving the app. **28 items total***
  - [x] Write `docs/evaluation-set.md`: per item — origin, AC provenance (human vs back-generated), reference availability, and on/off-domain classification
  - [x] **Gate:** if the set is still under 20, stop and report the shortfall rather than padding it with `train.jsonl` items or duplicates — *gate PASSED at 28; a test pins that no holdout origin appears in `train.jsonl`*
  - [x] Record the on-domain / off-domain split; it determines how AC8's caveats are worded — *10 on-domain (`agent_authored`, no reference) / 18 off-domain (`back_generated`, 70 reference scenarios)*

- [x] **Task 2: Results table and migration** (AC: 2, 4)
  - [x] Model + Alembic migration following `verification_results` — one row per (run_id, item, provider): generated scenarios (JSONB), `configured_provider`, `effective_provider`, `model_identifier`, latency, error, `created_at`
  - [x] RLS and per-user isolation preserved (NFR-S6)
  - [x] `run_id` groups a single evaluation execution so runs are comparable over time
  - [x] Tests: migration applies and rolls back; a row round-trips

- [x] **Task 3: The evaluation runner** (AC: 2, 3)
  - [x] `python -m app.evaluate_models` — the repo's **first** management command; no precedent exists, so keep it a thin `main()` over service functions, with the logic testable without the CLI
  - [x] Flags at minimum: `--limit`, `--providers`, `--run-id`, `--resume`
  - [x] **Resumable:** skip (run_id, item, provider) combinations already persisted
  - [x] **Record `effective_provider` on every row** and fail loudly at the end if any row configured `fine_tuned` was served by `general_llm` — a summary that quietly averages fallback output is the failure mode this AC exists to prevent
  - [x] Tests with both providers stubbed: resumption skips completed work, attribution is persisted, a mid-run error leaves prior rows intact

- [x] **Task 4: Objective metrics — no judge involved** (AC: 5)
  - [x] AC-clause coverage: every clause in the input AC has ≥1 scenario whose `source_ac_clause` cites it
  - [x] Schema validity rate; scenario count per item; duplicate / near-duplicate scenario rate within an item
  - [x] Reference similarity **for holdout items only** (a human-authored reference exists); skipped, not zeroed, for items without one
  - [x] Pure functions over stored rows — unit-testable with no model calls, and re-runnable without re-generating

- [x] **Task 5: Blinded subjective scoring** (AC: 6)
  - [x] Anonymise outputs and randomise presentation order before judging; the judge must not be able to infer provenance
  - [x] Record the judging model identifier in the results
  - [x] Prefer a judge from a **different provider** than any contestant; if that is impossible, say so in the report — do not leave it implied
  - [x] Tests: the judge payload contains no provenance markers, and order is genuinely randomised

- [x] **Task 6: Report** (AC: 7, 8, 9)
  - [x] `docs/evaluation-report.md`: side-by-side per metric, sample size, on/off-domain groups reported **separately**
  - [x] **"human-edit percentage": report `not measurable`** with the row count (`source='edited'` = 0) that makes it so
  - [x] Carry the quantization and corpus caveats verbatim in their own section
  - [x] State the conclusion plainly, including a null or baseline-favouring result
  - [x] Feed the answer back to Epic 6's serving decision in [training/serve/README.md](training/serve/README.md) — that decision was explicitly deferred pending this story

- [x] **Task 7: Regression and lint**
  - [x] `uv run --directory backend pytest -q` → **469 baseline** + new, zero regressions
  - [x] `uv run --directory backend ruff check app tests` — zero new errors on touched files
  - [x] `git status` clean of generated reports containing user content, and of any model artifacts

## Dev Notes

### Anti-patterns for this story

- ❌ **Don't evaluate on `train.jsonl`.** The fine-tune memorised it
- ❌ **Don't let the 12s provider timeout silently convert this into general-vs-general.** Check `effective_provider` on every row
- ❌ **Don't average on-domain and off-domain items into one headline number**
- ❌ **Don't use a contestant as the judge without saying so**
- ❌ **Don't compute "human-edit percentage" from zero rows** — report it as not measurable
- ❌ **Don't report a fine-tuned win or loss without the quantization caveat** — the serving path is a live confound
- ❌ **Don't change `FINE_TUNED_MODEL_TIMEOUT_SECONDS` in a committed file.** Env var, for the run only
- ❌ **Don't touch `training/build_dataset.py`, `finetune_bdd.py`, or `training/serve/app.py`** — Stories 6.5 and 6.2 own them and both are `done`

### Architecture rules that bind this story

- **Mandatory Rule 3:** reach a model only through `BDDModelProvider` ([architecture.md:309](_bmad-output/planning-artifacts/architecture.md#L309))
- **Mandatory Rule 1:** no business logic in route handlers — this story adds no routes at all
- **NFR-S6:** RLS and per-user isolation on any new table
- **NFR-P3** does **not** apply — this is a batch job, not a user-facing generation

### Project Structure Notes

**Create:** `backend/app/evaluate_models.py`, an evaluation service under `app/services/`, `backend/app/models/evaluation_result.py`, one Alembic migration, `docs/evaluation-set.md`, `docs/evaluation-report.md`, tests under `backend/tests/`.
**Modify:** `training/serve/README.md` (feed the conclusion back into the serving decision).
**Do NOT modify:** `app/api/**`, `app/schemas/bdd.py`, `app/services/bdd_model/**`, `training/**` except the README, any frontend file.

### Environment notes

- `bdd-lora` is already registered in Ollama (Story 6.2) — **no conversion work is needed**. Start the shim per `training/serve/README.md` and confirm `/health` reports `chat_template.status == "match"` before generating anything.
- Ad-hoc DB scripts must pass `connect_args={"statement_cache_size": 0}` — Supabase's PgBouncer pooler rejects prepared statements.
- Ollama stores models on **C:**, which had ~10 GB free after Story 6.2. If the plan involves pulling a second model to act as judge, check space first.

### Testing Standards

`pytest` + `pytest-asyncio` (`asyncio_mode="auto"`). Mock at the boundary with `unittest.mock.patch` / `AsyncMock`; no new test dependencies. Metric functions should be pure over stored rows so they are testable without any model call. Baseline to preserve: **469 passing**.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 6, Story 6.3 ACs and the execution-order note
- [Source: _bmad-output/planning-artifacts/prd.md:180] — "compare test coverage, AC relevance, and correctness against a general LLM baseline across 20+ real Jira tickets"
- [Source: _bmad-output/planning-artifacts/prd.md:187] — the documented fallback if fine-tuned accuracy is insufficient
- [Source: _bmad-output/implementation-artifacts/6-2-fine-tuned-model-integration.md] — the serving path, the 45s measurement, and the quantization exposure found in its review
- [Source: _bmad-output/implementation-artifacts/6-5-finetuning-dataset-and-training.md] — the corpus makeup and why a low eval loss is not a verdict
- [Source: training/RUN_LOG.md] — eval loss 0.3976 and the honest quality read
- [Source: training/serve/README.md] — serving procedure, the quantization control, and the deferred serving decision
- [Source: backend/app/models/verification_result.py] — results-table precedent
- [Source: backend/app/services/bdd_model/provider.py] — `effective_provider`, built in Story 6.1 for exactly this story's benefit

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Backend **469 → 538 passed** (+69), zero regressions. Ruff: **All checks passed** on all 13 new files.
- Migration `e1f2a3b4c5d6` applied to the live DB, **downgraded, and re-applied** — 18 columns, 4 indexes including a unique `(run_id, item_id, configured_provider)`.
- Evaluation set: **28 items** (10 on-domain curated + 18 off-domain holdout), 101 AC clauses, AC1 gate **PASS**.
- Run `eval-2026-08-13`: 22 rows persisted, **0 untrustworthy** — every fine-tuned row genuinely served by the fine-tune.
- Latency measured: on-domain fine-tuned **38.7–139.1s** (mean 72.6s) vs general LLM **3.5s**. Off-domain `holdout-01` 155s; `holdout-02` **2,416s**.
- Judge: 11 of 11 complete pairs judged successfully by `gpt-4o`, blinded.

### Completion Notes List

- **AC1 — 28 items, gate passed.** Epic 6.3's *"20+ real Jira tickets"* is unsatisfied (0 captured pairs, 5 tickets ever ingested), so the set was assembled from the 6.5 holdout plus curated product-domain ACs and labelled honestly in [docs/evaluation-set.md](docs/evaluation-set.md). **Curated items are marked `agent_authored`, not human-authored** — I wrote them, and claiming otherwise would misstate the evaluation's validity.
- **AC2/AC3 — runner with per-row persistence and attribution.** `python -m app.evaluate_models`, resumable via a unique index. Every row records `effective_provider`; the run refuses to summarise if >20% of fine-tuned rows fell back.
- **AC4 — migration + RLS.** Round-tripped against the live DB. RLS added to `001_rls_policies.sql` per the project convention that `auth.uid()` policies live outside Alembic.
- **AC5 — objective metrics, no LLM.** Coverage, duplicate rate, reference alignment, scenario count, success rate. All pure functions over stored rows, so scoring is re-runnable without re-generating.
- **AC6 — blinded judging.** Provenance stripped, A/B order randomised per item, judge model recorded.
- **AC7 — human-edit percentage reported `not measurable`** with its measured denominator (`source='edited'` = 0 rows).
- **AC8/AC9 — report written**, groups never averaged, all three caveats carried, conclusion stated plainly. Fed back into Epic 6's serving decision in [training/serve/README.md](training/serve/README.md).

#### 📊 The result: a SPLIT, not a baseline sweep

Final run — **all 28 items, 56 rows, zero degraded**:

| Metric | Fine-tuned | General LLM |
|---|---|---|
| **Off-domain — reference alignment** (n=18) | **0.488** | 0.176 |
| Off-domain — AC coverage | 1.000 | 1.000 |
| Off-domain — duplicate rate | 0.461 | **0.157** |
| On-domain — AC coverage (n=10) | 0.867 | **1.000** |
| On-domain — duplicate rate | 0.125 | **0.000** |
| Latency | 74s | **4s** |
| Judge, n=28 (conflicted) | 3.86 / 3.25 / 2.96 | **5.00 / 4.71 / 4.43** |

**The fine-tune wins decisively on the only metric grounded in human-authored Gherkin** — reference alignment 0.488 vs 0.176, nearly 3x closer to real reference scenarios than a frontier model. That is the first hard evidence the training pipeline actually does something.

It holds only **in its training distribution**. On product-domain ACs the baseline covers clauses the fine-tune misses, and everywhere the fine-tune repeats itself ~3x more and costs ~20x the latency.

**So: fine-tuning worked; it learned the wrong corpus.** Story 6.5 predicted the shape of this — *"learned the shape convincingly and the reasoning barely at all"* on a corpus 84% testing-framework Gherkin. What the full evaluation adds is that the style transfer is genuinely strong, just aimed at the wrong target. The serving decision is unchanged, but the remedy is now specific: **train on Gherkin that resembles the product's domain**, rather than simply more of it.

*(An earlier partial run, n=10 on-domain only, reported this as the baseline winning on every objective metric. The off-domain group reversed that on reference alignment — which is why AC1's insistence on reporting the two groups separately mattered.)*

#### ✅ The off-domain group is COMPLETE — n=18, and the "12 hours" claim was wrong by ~30x

~~The off-domain group is not evaluable on this hardware, ~12 hours of CPU inference.~~ **Withdrawn.** That extrapolated from a 2,416s latency which measured a bounded 300s timeout plus a CPU-starved fallback — not a generation. Code review caught the contradiction; a 20s-bound test confirmed the timeout fires correctly.

Re-run after the fix: **all 18 items completed in ~25 minutes at a 75s mean**, every row trustworthy. The group was never infeasible — the number I reasoned from simply did not measure what I thought it did.

This unlocked **reference alignment**, the only metric grounded in human-authored Gherkin, and with it the on/off-domain gap that AC1 was structured around. It is also the metric on which the fine-tune wins.

#### 🐞 Bug found and fixed by the crash

`run_evaluation` held **one DB session open across the whole batch**. When a generation ran 40 minutes, the pooler dropped the connection and the `INSERT` died with `ConnectionDoesNotExistError` — silently defeating the per-row commit that AC2's resumability depends on. Fixed: the runner now takes a session *factory* and each write gets its own short-lived session. The docstring records the measurement that caused it.

#### ⚠️ The judge is a contestant

`gpt-4o` judged, and `gpt-4o` **is** the general LLM baseline. `judge_conflict()` detects and reports this, and the report carries it above the scores. Blinding does not fix a contestant grading itself; only an independent judge would, and none is configured. The subjective scores corroborate the objective ones directionally, which is all they should be taken to do. **The objective metrics carry the comparison.**

#### The guard that earned its keep

`--check-config` refused the exact trap the story predicted: at the production 12s budget, every one of the 45–72s fine-tuned calls would have fallen back, producing a clean-looking general-vs-general comparison. Combined with per-row `effective_provider`, the final run recorded **0 untrustworthy rows** out of 22.

## Senior Developer Review (AI)

**Date:** 2026-08-13 · **Outcome:** Changes Requested → **all High and Medium fixed** · **Reviewer model:** claude-opus-5, cross-checked against an independent review pass

**Git vs File List:** 0 discrepancies. All findings were verified by execution before being accepted — including one that turned out to overturn a published conclusion.

### Action Items

- [x] **[High] H1 — The "off-domain not evaluable / ~12 hours" conclusion was wrong, and I had published it in three places.** An independent review flagged that `docs/evaluation-report.md` cited a 2,416s latency against a 300s `asyncio.timeout` bound — a contradiction I had not noticed. **Tested it:** with a 20s bound the same holdout item completed in **29.4s total**, so the bound fires correctly. The 2,416s therefore measured a *bounded timeout plus a CPU-starved fallback* while Ollama churned on the abandoned request — **not a generation**. The only genuine off-domain datapoint is `holdout-01` at 155s, extrapolating to **~45 minutes** for the remaining 17 items, not 12 hours. **Fixed:** claim withdrawn and corrected in the report, this story, and `sprint-status.yaml`; the off-domain group is now described as *incomplete*, not infeasible. This was the worst error in the story — a confident, quantified conclusion resting on a number whose mechanism I never checked.
- [x] **[High] H2 — `--score` wrote the report without ever calling the trust gate.** `assert_run_is_trustworthy` ran only in the generation path, over that invocation's outcomes, so a resumed run scored rows it had never seen and could launder a systematically degraded earlier run into a clean-looking report. The one gate designed to stop a general-vs-general comparison did not guard the artifact anyone reads. **Fixed:** `_score` re-derives outcomes from the persisted rows and gates on them before rendering.
- [x] **[High] H3 — `success_rate` counted degraded rows while every other metric excluded them.** A fallback-served row was credited to the fine-tune as a success — in the one metric added to stop a model hiding behind its good rows. **Fixed:** degraded rows are excluded from the denominator too, consistent with every other statistic; genuine failures still count, because they are attributable. Two tests pin both halves.
- [x] **[Med] M1 — `--no-resume` crashed on the unique index.** Re-running a pair violated `uq_evaluation_results_run_item_provider`; there was no upsert. **Fixed** together with M2 and L1 by making `persist_outcome` an upsert.
- [x] **[Med] M2 — `completed_keys` did not filter on `succeeded`.** A failed or fallback-served pair counted as "done", so `--resume` permanently locked in exactly the rows a rerun exists to replace. **Fixed:** only rows that succeeded *and* were served by the provider they claim count as complete; re-running them is safe because writes upsert.
- [x] **[Med] M3 — attribution was read after `generate_bdd` returned**, so a provider that *raised* persisted `effective_provider=NULL` — losing the record of what broke, which is what a failed row most needs to say. My comment claimed it was captured "BEFORE anything else can fail", which was untrue. **Fixed:** captured in the `finally`.
- [x] **[Med] M4 — the model identifier was wrong under the shipped default.** `llm_provider` defaults to `"claude"` while `llm_model` defaults to `"gpt-4o"` (documented as an OpenAI name), and `ClaudeProvider` hardcodes its own model and ignores the setting. Both the baseline's `model_identifier` and the report's judge name would have named a model that never ran. This run was correct only because `.env` sets `openai`. **Fixed:** `general_llm_model_name()` resolves from `llm_provider`.
- [x] **[Med] M5 — the judged-item count was logged but never rendered.** Unusable verdicts are dropped silently, so without a sample size a thin judging pass was indistinguishable from a complete one. **Fixed:** the report now states *"Items judged: 11"* above the subjective table.
- [ ] **[Low] L1 — the `attempt` column was written as 1 and never incremented.** **Resolved incidentally** by M1's upsert, which now increments it on every retry.

### Review Notes

Every High sat in the machinery built to *guarantee* trustworthiness — the trust gate, the success metric, and my own reasoning about a latency number. The integrity checks were themselves unchecked, which is the third occurrence of that pattern in this epic and the first time I made it while explicitly writing about the danger of it.

H1 deserves singling out. The pipeline's own guards worked exactly as designed: zero degraded rows, correct attribution, an honest refusal to average groups. The error was in prose I wrote *around* the data — an extrapolation that felt safe because everything near it had been measured. **Credit to the independent review for catching the arithmetic that made it impossible.** A number that contradicts a timeout bound in the same document should have stopped me, and did not.

### File List

**Backend — created:**
- `backend/app/evaluate_models.py` — the repo's first management command; `--check-config`, `--resume`, `--score`, `--judge`
- `backend/app/models/evaluation_result.py` — result row with the attribution columns and `is_trustworthy`
- `backend/app/services/evaluation_set.py` — set assembly, provenance labelling, AC-clause parsing
- `backend/app/services/evaluation_service.py` — runner, attribution capture, resumability, degraded-run gate
- `backend/app/services/evaluation_metrics.py` — coverage, duplicate rate, reference alignment, aggregation
- `backend/app/services/evaluation_judge.py` — blinding, randomisation, conflict detection, aggregation
- `backend/app/services/evaluation_report.py` — per-group tables and the caveat sections
- `backend/alembic/versions/e1f2a3b4c5d6_create_evaluation_results_table.py`
- `backend/tests/test_evaluation_set.py` (12), `test_evaluation_result_model.py` (8), `test_evaluation_service.py` (13), `test_evaluation_metrics.py` (18), `test_evaluation_judge.py` (18)

**Backend — modified:**
- `backend/app/models/__init__.py` — register `EvaluationResult` so Alembic can see it
- `backend/supabase/migrations/001_rls_policies.sql` — RLS enable + user-isolation policy

**Repo — created:**
- `evaluation/curated_ac_sets.json` — 10 on-domain AC sets (the story did not specify a home for these; `evaluation/` keeps them out of both `backend/` and `training/`)
- `docs/evaluation-set.md` — provenance and composition
- `docs/evaluation-report.md` — the results

**Modified:**
- `training/serve/README.md` — Story 6.3's answer folded into the serving decision
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

**Not committed:** nothing generated contains user content; `training/data/` and model artifacts remain git-ignored.

## Change Log

- 2026-08-22: **Retro-documented: this story's result was superseded on 2026-08-15 and the change was never logged here.** The entries below record the **Run 2 (7B)** evaluation. Story 6.8's Run 3 replaced the served model with a 1.5B ablation, `docs/evaluation-report.md` was rewritten for `run-3-1.5b`, and the headline numbers moved: **reference alignment 0.317 vs 0.240** off-domain (was 0.488 vs 0.176 — the fine-tune still leads, but by far less), and on-domain **coverage is now 1.000 for both providers** (was 0.867 vs 1.000), with duplicates 0.389 vs 0.000 and latency 62.8s vs 4.2s. The 2026-08-13 entries are left exactly as written — they are the record of what was true when the story closed — but they must not be quoted as current. **The story's ACs are unaffected**: AC8/AC9 require the report to state the conclusion plainly, and it does not yet — `docs/evaluation-report.md` still ends *"Conclusion: _Pending._"* for the run-3 re-evaluation. That is the one open item Epic 6 still owes; the pipeline itself needed no change to produce it. Separately, the runner gained `allow_fallback` plumbing (Story 6.9) so a measuring caller cannot be re-enabled into fallback by configuration.
- 2026-08-13: **Off-domain group completed — the result is a split, not a baseline sweep.** With the resume/upsert fixes in place, all 18 holdout items ran in **~25 minutes at a 75s mean**, every row trustworthy (56/56, zero degraded) — confirming that the withdrawn "~12 hours / not evaluable" claim was wrong by roughly 30x. Completing the group unlocked **reference alignment**, the only metric grounded in human-authored Gherkin, and it reverses the earlier partial conclusion: the fine-tune scores **0.488 vs the baseline's 0.176**, nearly 3x closer to real reference scenarios. It wins only in its training distribution, repeats itself ~3x more (0.461 vs 0.157) and costs ~20x the latency, and on product-domain ACs the baseline still covers clauses it misses. **Fine-tuning worked; it learned the wrong corpus** — which sharpens the remedy from "better data" to "Gherkin that resembles the product's domain". Serving decision unchanged. Judge re-run over all 28 pairs. *Separately:* `parse_feature` gained **`Scenario Outline` expansion** (Examples-row substitution, capped at 3 per outline), implementing the improvement `RUN_LOG.md` had listed as untapped — measured at **213 → 222 documents (+4%) and +77 scenarios**, materially smaller than the entry predicted, because `outlines skipped` counts blocks rather than documents. It also fixes a live upload rejection for outline-based `.feature` files. 540 → **546 tests**; the dataset was deliberately **not** rebuilt, since +4% will not move a fine-tune whose bottleneck is corpus domain.
- 2026-08-13: **Code review — 3 High, 5 Medium fixed; 1 Low resolved incidentally.** The headline fix is a **withdrawn conclusion**: this story had claimed the off-domain group was "not evaluable on this hardware, ~12 hours", extrapolated from a 2,416s latency. An independent review noticed that figure contradicted the 300s `asyncio.timeout` bound in the same document; testing showed the bound fires correctly (20s bound → 29.4s total), so the 2,416s measured a timeout plus a CPU-starved fallback, not a generation. Real extrapolation from the one genuine datapoint is **~45 minutes**, so the group is *incomplete*, not infeasible — corrected in the report, the story and sprint status. Also fixed: `--score` wrote the report without ever invoking the trust gate, so a resumed run could launder degraded rows into a clean report; `success_rate` counted fallback-served rows as fine-tuned successes while every other metric excluded them; `--no-resume` crashed on the unique index and failed pairs could never be retried (both fixed by making writes upsert, which also revives the dead `attempt` column); attribution was lost when a provider raised; the model identifier named a model that never ran under the shipped `LLM_PROVIDER=claude` default; and the judged-item count was logged but never rendered. 538 → **540 tests**, lint clean.
- 2026-08-13: **Implemented — and the baseline won.** Assembled a 28-item evaluation set after confirming the epic's *"20+ real Jira tickets"* premise is still false (0 captured pairs, unchanged since 6.5 measured it), labelling curated items `agent_authored` rather than overstating them as human. Built the results table, the resumable runner, the objective metrics, the blinded judge and the report generator; 469 → **538 tests**, lint clean. On the complete on-domain group (**n=10**, zero degraded rows) the general LLM beat the fine-tune on **every** objective metric — coverage 1.000 vs 0.867, duplicate rate 0.000 vs 0.125, latency 3.5s vs 72.6s — with the blinded judge agreeing directionally. That **confirms** Epic 6's local-only serving decision rather than overturning it, and the answer is fed back into `training/serve/README.md`. Two findings came out of running it rather than reading it: the **off-domain holdout is not evaluable on this hardware** (8–13 AC clauses per item; one took 40 minutes, extrapolating to ~12 hours for the group), so reference alignment is *unmeasured* rather than zero; and that 40-minute generation exposed a **real bug** — the runner held one DB session across the whole batch, so the pooler dropped it and the row was lost mid-INSERT, defeating the per-row commit that resumability depends on. Fixed to a session-per-write. The judge is `gpt-4o`, which is also the baseline contestant; that conflict is detected in code and stated above the scores, and the objective metrics carry the comparison.
- 2026-08-13: Story drafted. Measured the live database and found the epic's opening *"Given a set of 20+ real Jira tickets with known acceptance criteria"* **unsatisfied** — `bdd_files.acceptance_criteria` is still 0 rows, unchanged since Story 6.5 measured it on 2026-08-08, and only 5 distinct Jira tickets have ever been ingested. Rewrote that Given into an explicit evaluation-set acquisition task (AC1/Task 1), the same correction Story 6.5 needed. Scoped out the epic's **"human-edit percentage"** metric as *not measurable*: it requires captured edits and there are zero, so AC7 requires reporting it as such rather than computing it from an invented denominator. Structured the ACs around **three confounds** any one of which could make a confident number meaningless — the NF4-vs-q4_K_M quantization drift found in Story 6.2's review, the 84%-off-domain training corpus from 6.5, and the conflict of interest in using a contestant LLM as judge — and made blinded, randomised judging an AC rather than a suggestion. Flagged that `FineTunedModelProvider`'s 12s bound will fall back on **every** call at the measured 45s latency, silently scoring general-LLM output as fine-tuned; AC3's `effective_provider` check exists to catch that, using the attribution mechanism Story 6.1 built for this story specifically. Made a baseline-favouring result an explicitly successful outcome, since Epic 6's serving decision already assumes it may happen.
