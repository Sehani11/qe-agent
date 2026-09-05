# Sprint Change Proposal — Epic 6 Training Gap

**Date:** 2026-08-08
**Raised by:** Chamath
**Workflow:** `correct-course` (Batch mode)
**Status:** ✅ Approved and applied — 2026-08-08

---

## 1. Issue Summary

**Epic 6 plans to integrate and evaluate a fine-tuned model, but no story produces one — and the data it would be trained on is being discarded today.**

The gap surfaced while closing Story 6.1. With the `BDDModelProvider` abstraction done, the next story (6.2) opens with *"**Given** a fine-tuned model is deployed and accessible via HTTP endpoint"* — a precondition nothing in the plan satisfies. Tracing it back revealed two distinct problems:

**a. No story owns training.** Epic 6's three stories abstract (6.1 ✅), integrate (6.2), and evaluate (6.3). [epics.md:190](_bmad-output/planning-artifacts/epics.md#L190) assigns training to *"model training infrastructure (external)"*, and [architecture.md:214](_bmad-output/planning-artifacts/architecture.md#L214) lists *"Fine-tuned model training pipeline infrastructure"* under **Deferred Decisions**. Between them, the work fell through the crack: deferred by architecture, externalised by epics, owned by nobody.

**b. The training data is not being captured — and cannot be captured retroactively.** [prd.md:168](_bmad-output/planning-artifacts/prd.md#L168) specifies a model *"trained on **real story-to-test-case pairs**"*. The application stores neither side of that pair cleanly:

| Required signal | Current state |
|---|---|
| Acceptance criteria that drove a generation | ❌ Not persisted — [session.py:19](backend/app/models/session.py#L19) stores only `jira_ticket_id` |
| Resulting Gherkin | ✅ `bdd_files.content` |
| Human corrections to generated Gherkin | ❌ Discarded — [bdd.py](backend/app/api/v1/bdd.py) exposes only `/generate` and `/upload`; editor edits never leave the browser |

This is the time-sensitive half. Every session run today throws away a training pair that cannot be recovered later — Jira tickets drift after the fact, and a correction not captured at the moment of editing is gone. **Deferring capture has a compounding cost that deferring training does not.**

### Evidence

- Story 6.2 AC1 presumes a deployed endpoint; no story creates one
- `prd.md:168` mandates real paired data; no story persists the input side
- Supabase DB is currently unreachable (`tenant/user postgres.qinhim… not found`), so even historic rows could not be counted — the corpus is unverified and likely near-empty
- A dataset builder (`training/build_dataset.py`) was written during this session and is currently owned by **no story**

---

## 2. Impact Analysis

### Epic Impact

| Epic | Impact |
|---|---|
| **Epic 6** | **Modify.** Scope expands from integrate+evaluate to capture→train→integrate→evaluate. Two stories added. Title and dependencies no longer accurate. |
| Epic 1 | **Touched, not reopened.** Data capture modifies `sessions`/`bdd_files` and `api/v1/bdd.py`, which Epic 1 delivered. Executed as Epic 6 work under Epic 6's rationale; Epic 1 stays `done`. |
| Epics 2–5 | **None.** No dependency on BDD model selection. |

### Story Impact

- **6.1** — none. Completed and unaffected; its `BDDModelProvider` contract is what 6.5 must satisfy.
- **6.2** — remains blocked, but the blocker becomes *tracked* (depends on new 6.5) rather than invisible. **Second blocker identified below.**
- **6.3** — unchanged in scope; gains a real model to compare against.
- **New 6.4** — Training Data Capture.
- **New 6.5** — Fine-Tuning Dataset & Model Training.

### Artifact Conflicts

| Artifact | Conflict | Action |
|---|---|---|
| `epics.md` | Epic 6 title/description/dependencies exclude training; no 6.4/6.5 | Update Epic List entry + Epic 6 section; add two stories |
| `architecture.md` | `:214` defers training infra post-MVP, which is what orphaned the work | Amend: capture is in-scope **now**; training/serving remain Phase 2 |
| `architecture.md` | No record of where a fine-tuned model would be *served* | Add serving constraint (see Technical Impact) |
| `prd.md` | **No conflict.** `:286` states MVP can ship on the general LLM | No change |
| UX spec | **No conflict.** No user-facing surface changes | No change |
| `sprint-status.yaml` | Missing 6-4/6-5 | Add, sequenced before 6-2 |

### Technical Impact

**⚠️ A second, previously untracked blocker on 6.2.** Production runs on **`t3.small` — 2 vCPU, 2 GB RAM, no GPU** ([setup-infra.sh:247](cfn/setup-infra.sh#L247)), and [qe-agent.yaml:23](cfn/qe-agent.yaml#L23) permits only t3 sizes. A 7B model cannot load there, let alone answer inside the 12s budget. So 6.2 has **two** unmet dependencies: no model exists, *and* nowhere to serve it. Serving is the recurring cost (~$700+/month for a GPU instance vs ~$15/month today) and is the decision that determines whether Epic 6 is viable at all.

**Mitigating factor:** Story 6.1's fallback design means an absent or cold endpoint degrades to the general LLM and logs `reason=endpoint_error` rather than failing. Scale-to-zero serving is therefore tolerable, which widens the options.

**Not blocking 6.3.** Evaluation can run the fine-tune locally through the existing Ollama provider — so the "is it actually better?" question can be answered before committing to any serving spend.

Other technical notes: 6.4 requires an Alembic migration (RLS must be preserved); training dependencies must never enter `backend/pyproject.toml` (the Dockerfile's `COPY . .` would ship them); `training/` sits at repo root, outside the build context.

---

## 3. Recommended Approach

**Option 1 — Direct Adjustment.** Add two stories inside the existing Epic 6 structure.

| Option | Verdict |
|---|---|
| **1. Direct Adjustment** | ✅ **Selected.** Effort: Medium. Risk: Low. |
| 2. Rollback | ❌ Not viable. Nothing to roll back — 6.1 is correct and needed either way. |
| 3. MVP Review | ❌ Not applicable. `prd.md:286` already exempts MVP from fine-tuned model readiness. |

**Rationale.** The plan's structure is sound; it has a hole, not a flaw. Epic 6 is already `in-progress` with a clean foundation, and the missing work is two well-bounded stories. Adding them preserves every existing reference (renumbering would invalidate "Story 6.2"/"6.3" citations inside the completed 6.1 file) while making the true execution order explicit.

**Sequencing note.** Execution order is 6.4 → 6.5 → 6.2 → 6.3, which is not numeric order. `create-story` selects the first `backlog` entry top-to-bottom in `sprint-status.yaml`, so the new entries are placed **above** 6-2 in the file. Numbers stay stable; the file expresses the real order.

**MVP impact: none.** Epic 6 is Phase 2 throughout. The one nuance worth stating plainly: **Story 6.4 should ship early even though training is Phase 2**, because it is the only story whose value decays with delay.

---

## 4. Detailed Change Proposals

### 4.1 `epics.md` — Epic List entry (line 187)

**OLD**
```
### Epic 6: Fine-Tuned Model Integration (Phase 2)
Integrate a fine-tuned domain-specific model for BDD test case generation, with a provider abstraction that allows seamless switching between the fine-tuned model and the general-purpose LLM. Includes evaluation pipeline for systematic comparison.
**FRs covered:** FR13, FR14 (enhanced)
**Dependencies:** Epic 1 (BDD generation API must exist), model training infrastructure (external)
**Note:** This epic is Phase 2 scope. The general LLM continues to serve BDD generation in MVP. The `BDDModelProvider` abstraction is set up early so that the fine-tuned model can be plugged in when ready.
```

**NEW**
```
### Epic 6: Fine-Tuned Model Training & Integration (Phase 2)
Capture real story-to-test-case training pairs, train a fine-tuned domain-specific model for BDD test case generation, and integrate it behind a provider abstraction that allows seamless switching between the fine-tuned model and the general-purpose LLM. Includes evaluation pipeline for systematic comparison.
**FRs covered:** FR13, FR14 (enhanced)
**Dependencies:** Epic 1 (BDD generation API must exist). Model training runs on external GPU compute (Kaggle/Colab); all datasets, configs and scripts are versioned in this repo.
**Note:** This epic is Phase 2 scope and the general LLM continues to serve BDD generation in MVP — **except Story 6.4, which should ship early**. Training pairs cannot be captured retroactively, so every release without it permanently loses data. The `BDDModelProvider` abstraction (6.1) is already in place.
**Execution order:** 6.4 → 6.5 → 6.2 → 6.3 (not numeric order; see sprint-status.yaml).
```

**Rationale:** Names the training work, removes the "external" phrasing that orphaned it, and records the ship-early exception plus the true sequence.

### 4.2 `epics.md` — Epic 6 section note (line 838)

**OLD**
```
> **Note:** This is Phase 2 scope. The `BDDModelProvider` abstraction interface is set up early (Story 6.1) so that the fine-tuned model can be plugged in when the trained model is available. The general LLM continues to serve BDD generation in MVP via `GeneralLLMFallbackProvider`.
```

**NEW**
```
> **Note:** This is Phase 2 scope. The `BDDModelProvider` abstraction interface is set up early (Story 6.1) so that the fine-tuned model can be plugged in when the trained model is available. The general LLM continues to serve BDD generation in MVP via `GeneralLLMFallbackProvider`.
>
> **Story 6.4 is the exception to "Phase 2".** It captures the training pairs that Stories 6.5–6.3 depend on, and that data cannot be recovered after the fact — acceptance criteria are not persisted and editor corrections are discarded. Ship it with MVP even though the model itself is Phase 2.
>
> **Serving is an open decision.** Production is `t3.small` (no GPU) and cannot host a 7B model. Story 6.2 is blocked on a serving decision as well as on model availability. Story 6.3 can evaluate locally via the Ollama provider without resolving it.
```

**Rationale:** Records both blockers on 6.2 at the epic level so neither is rediscovered mid-story.

### 4.3 `epics.md` — Insert two stories after Story 6.3

```
### Story 6.4: Training Data Capture

As a **researcher**,
I want every BDD generation to persist its input acceptance criteria and any human corrections,
So that a dataset of real story-to-test-case pairs accumulates from normal usage.

**Acceptance Criteria:**

**Given** a BDD generation request succeeds
**When** the resulting `bdd_files` row is written
**Then** the acceptance criteria text that produced it is persisted and linked to that row, forming a retrievable input→output pair

**And** when a user edits generated BDD in the editor and saves, a row is persisted with `source='edited'` that references the originating generated row, preserving both versions
**And** existing `generated` and `uploaded` behaviour is unchanged — no regression to Epic 1 flows
**And** an Alembic migration adds the new column(s) with RLS and per-user isolation preserved (NFR-S6)
**And** Pytest tests verify AC persistence, that an edited save creates a distinct row, and that the original generated content survives

---

### Story 6.5: Fine-Tuning Dataset & Model Training

As a **researcher**,
I want a reproducible dataset build and training run,
So that a fine-tuned BDD model exists for Story 6.2 to integrate and Story 6.3 to evaluate.

**Acceptance Criteria:**

**Given** a corpus of `.feature` files and/or captured `bdd_files` rows
**When** `training/build_dataset.py` runs
**Then** it emits `train.jsonl` / `holdout.jsonl` in chat format, every target validated against `BDDGenerateResponse`, empty scenario arrays rejected, and the split taken by origin file so no scenario appears on both sides

**And** the training configuration is committed under `training/` and completes on free-tier GPU (Kaggle/Colab), producing a LoRA adapter
**And** training dependencies are NOT added to `backend/pyproject.toml` — they would ship in the production image via the Dockerfile's `COPY . .`
**And** the run is documented (base model, hyperparameters, dataset size, eval loss) so it can be reproduced
**And** the resulting model is servable behind the contract `FineTunedModelProvider` expects: `{acceptance_criteria, system_prompt, response_format}` in, `BDDGenerateResponse`-shaped JSON out, within `FINE_TUNED_MODEL_TIMEOUT_SECONDS`
```

**Rationale:** 6.4 is bounded and shippable now. 6.5 absorbs the existing `training/build_dataset.py`, which is otherwise untracked work, and pins the contract 6.1 established.

### 4.4 `architecture.md` — Deferred Decisions (line 214)

**OLD**
```
- Fine-tuned model training pipeline infrastructure
```

**NEW**
```
- Fine-tuned model training compute (runs on external GPU — Kaggle/Colab; datasets and configs are versioned in `training/`)
- Fine-tuned model **serving** infrastructure — unresolved. Production is `t3.small` (no GPU) and cannot host a 7B model; Story 6.2 requires this decision. Note training-data **capture** is NOT deferred (Story 6.4) — pairs cannot be captured retroactively.
```

**Rationale:** This line is the root cause. Splitting compute / serving / capture prevents the same ambiguity from orphaning the work again.

### 4.5 `sprint-status.yaml` — Epic 6 entries

**OLD**
```
  epic-6: in-progress  # Fine-Tuned Model Integration (Phase 2)
  6-1-bdd-model-provider-abstraction: done  # ...
  6-2-fine-tuned-model-integration: backlog
  6-3-model-evaluation-pipeline: backlog
```

**NEW**
```
  epic-6: in-progress  # Fine-Tuned Model Training & Integration (Phase 2)
  # Execution order, not numeric order: 6.4 and 6.5 must precede 6.2.
  6-1-bdd-model-provider-abstraction: done  # ...
  6-4-training-data-capture: backlog  # ship early — pairs cannot be captured retroactively
  6-5-finetuning-dataset-and-training: backlog  # absorbs training/build_dataset.py
  6-2-fine-tuned-model-integration: backlog  # blocked: needs 6.5 AND a serving decision
  6-3-model-evaluation-pipeline: backlog  # can evaluate locally via Ollama
```

**Rationale:** Order drives `create-story` auto-discovery, so the file must express execution order. Comments record the blockers inline.

---

## 5. Implementation Handoff

**Scope classification: Moderate** — backlog reorganisation across planning artifacts, no fundamental replan.

| Recipient | Responsibility |
|---|---|
| **Scrum Master** (`create-story`) | Draft Story 6.4 with full context, then 6.5. 6.4 first. |
| **Dev** (`dev-story`) | Implement 6.4 — migration, capture, save endpoint, tests. |
| **You / researcher** | Run the Kaggle training for 6.5; commit config + documented results. |
| **Architect** | Decide fine-tuned model **serving** before 6.2 is drafted. Unblocks nothing else. |

**Success criteria:** 6.4 merged and capturing pairs in normal usage; `build_dataset.py` producing validated JSONL from real captured data; a documented, reproducible training run yielding a servable adapter; 6.2 unblocked only once serving is decided.

### Open items carried out of this analysis

- **[Action-needed]** Serving infrastructure decision — blocks 6.2, no owner yet
- **[Action-needed]** Supabase project unreachable — confirm whether it is paused or gone; 6.4's migration needs a live DB
- **[Out of scope]** `Settings()` rejects the repo-root `.env` and prints credential values in the traceback (including AWS keys). Real exposure risk in CI logs. Deserves its own story; unrelated to this change.

---

## Checklist Status

| Section | Status |
|---|---|
| 1. Trigger and context | [x] Done — trigger, categorisation (misunderstanding of original requirements), evidence recorded |
| 2. Epic impact assessment | [x] Done — Epic 6 modified, no epic invalidated, no new epic needed, order adjusted |
| 3. Artifact conflict analysis | [x] Done — epics/architecture/sprint-status conflict; PRD and UX clear |
| 4. Path forward evaluation | [x] Done — Option 1 Direct Adjustment selected |
| 5. Proposal components | [x] Done |
| 6. Final review and handoff | [x] Done — approved 2026-08-08; all edits applied to `epics.md`, `architecture.md`, `sprint-status.yaml` |
