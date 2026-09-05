# Sprint Change Proposal — Epic 6 Serving-Verification Boundary

**Date:** 2026-08-09
**Raised by:** Chamath
**Workflow:** `correct-course` (Batch mode)
**Trigger:** Code review of Story 6.5, 2026-08-09
**Status:** ✅ Approved and applied — 2026-08-09

---

## 1. Issue Summary

**Epic 6 asks Story 6.5 to prove the fine-tuned model is servable, while simultaneously assigning the serving decision to Story 6.2. Story 6.5 cannot satisfy an AC whose prerequisite another story owns.**

Story 6.5's final acceptance criterion in [epics.md](epics.md) reads:

> **And** the resulting model is servable behind the contract `FineTunedModelProvider` expects: `{acceptance_criteria, system_prompt, response_format}` in, `BDDGenerateResponse`-shaped JSON out, within `FINE_TUNED_MODEL_TIMEOUT_SECONDS`

But the same Epic 6 preamble says:

> **Serving is an open decision.** Production is `t3.small` (no GPU) and cannot host a 7B model. Story 6.2 is blocked on a serving decision as well as on model availability.

And [architecture.md:216](architecture.md#L216) lists *"Fine-tuned model **serving** infrastructure — unresolved… Story 6.2 requires this decision"* under Deferred Decisions.

**The plan contains both statements.** 6.5 is required to demonstrate servability; 6.2 owns whether serving exists at all. This is a boundary defect in the plan, not a failure of the work.

### How it surfaced

Story 6.5 delivered everything else and was moved to `review`. The adversarial code review on 2026-08-09 found its Task 7 subtask — *"re-run Task 5's shim against the real model and confirm a genuine `BDDGenerateResponse` comes back inside 12s"* — marked `[x]` when it had never been done. It was un-ticked, and investigating **why** it had never been done exposed the boundary problem rather than a dev oversight.

### Evidence

| Fact | Source |
|---|---|
| Training produces a **PEFT LoRA adapter**, not a servable endpoint | `training/outputs/outputs/bdd-lora/adapter_model.safetensors`, 161 MB |
| Ollama cannot load a PEFT adapter — conversion to GGUF is required first | `ollama list` is empty; no `convert_lora_to_gguf` output exists |
| The planned bridge was deleted mid-story | Unsloth's GGUF/`Modelfile` export was removed in attempt 8 when unsloth broke on Kaggle ([RUN_LOG.md](../../training/RUN_LOG.md)) |
| No machine available can meet the 12s budget | Dev GPU is an **MX330, 2 GB VRAM**; a 7B q4_K_M needs ~5 GB, so inference falls to CPU at ≈30–90s per generation |
| This was predicted, in writing, before the review | [serve/README.md](../../training/serve/README.md): *"A 7–8B model on CPU will not make that budget"* |
| No story owns the adapter → servable-artifact conversion | Absent from 6.5, 6.2 and 6.3 alike |

**Issue category:** *Misunderstanding of original requirements* — specifically, an AC placed in the wrong story. Not a technical limitation, not a strategic pivot, and **not a failed approach**: the model trained successfully (eval loss 0.3976) and every other 6.5 deliverable is complete and verified.

### Secondary defect found in the same area

Story 6.2's AC states BDD generation *"completes within **30 seconds**"*, but [config.py:43](../../backend/app/core/config.py#L43) sets `fine_tuned_model_timeout_seconds = 12.0`, enforced as a total bound by `asyncio.timeout` so the general-LLM fallback still fits inside NFR-P3's 30s. Story 6.5 discovered this and built against 12s. **The epic still carries the wrong number**, and 6.2 will be written from the epic.

---

## 2. Impact Analysis

### Epic Impact

| Epic | Impact |
|---|---|
| **Epic 6** | **Modify.** Scope, story count and sequence all unchanged. One AC clause moves from 6.5 to 6.2, one prerequisite gains an owner, one timeout figure is corrected. |
| Epics 1–5 | **None.** All `done`; no dependency on BDD model selection or serving. |

Epic 6 remains completable as planned. No epic is invalidated, none is added, and the execution order (6.4 → 6.6 → 6.7 → 6.5 → 6.2 → 6.3) is unaffected — 6.2 already precedes 6.3.

### Story Impact

| Story | Status | Impact |
|---|---|---|
| **6.5** | `in-progress` → **`done`** | Final AC narrowed to what 6.5 can own: a shim implementing the contract, unit-tested, with the training chat template verifiable. The end-to-end proof moves out. |
| **6.2** | `backlog` | **Gains three things:** the artifact conversion, the end-to-end shim verification, and a corrected 12s budget. **Partially unblocked** — "needs 6.5" is satisfied; only the *production* serving decision remains open. |
| **6.3** | `backlog` | **Unchanged in scope, but its dependency is now explicit.** 6.3 evaluates fine-tuned vs general LLM locally via Ollama — which needs the converted artifact 6.2 now owns. Previously nobody was going to produce it. |
| 6.1, 6.4, 6.6, 6.7 | `done` | None. |

⚠️ **The conversion gap is the same class of defect as the 2026-08-08 proposal found**: work that every downstream story assumes and no story performs. Assigning it explicitly is the substantive part of this change.

### Artifact Conflicts

| Artifact | Conflict | Action |
|---|---|---|
| `epics.md` — Story 6.5 AC | Requires servability that 6.2 gates | Narrow the clause |
| `epics.md` — Story 6.2 AC | Says 30s; the configured budget is 12s | Correct, citing `config.py:43` |
| `epics.md` — Epic 6 preamble | Says serving is 6.2's decision — **correct**, and the reason 6.5's AC was misplaced | Add a line naming 6.2 as owner of conversion |
| `architecture.md:215-216` | Accurate but stale: a trained adapter now exists, and its location is undocumented | Amend to record the artifact and its path |
| `6-5-…md` story file | AC6 + Task 7's open subtask | Narrow AC6; close Task 7; status → `done` |
| `sprint-status.yaml` | 6-5 `in-progress`; 6-2's blocker comment now half-wrong | Update both |
| `prd.md` | **No conflict.** The PRD requires a fine-tuned model trained on real pairs; it does not specify which story verifies serving | None |
| `ux-design-specification.md` | **No impact.** No UI surface involved | None |
| CI / deployment / tests | **No impact.** Nothing here changes the backend image, pipelines or the test suite | None |

### Technical Impact

None to shipped code. `BDD_MODEL_PROVIDER` defaults to `general_llm`, so BDD generation continues unchanged for every user. The shim lives under `training/` and is not part of the backend image.

---

## 3. Recommended Approach

### Options evaluated

| Option | Verdict | Effort | Risk |
|---|---|---|---|
| **1. Direct Adjustment** — move the AC clause to 6.2, assign conversion, correct the timeout | ✅ **Selected** | **Low** | **Low** |
| 2. Potential Rollback | ❌ Not viable | — | — |
| 3. PRD MVP Review | ❌ Not applicable | — | — |

**Why not rollback:** there is nothing to roll back. Story 6.5's corpus, dataset, JSON branch, `Background:` parser fix, config, notebook, training run and shim are all sound and independently verified (454 tests passing). The defect is where one sentence sits in the plan.

**Why not MVP review:** Epic 6 is explicitly Phase 2 and outside the MVP. [prd.md:187](prd.md#L187) already carries the contingency — *"Fine-tuned model accuracy insufficient → Fallback: continue using general LLM with enhanced prompting"* — and that fallback is the shipped default today. **MVP is unaffected.**

**Why Direct Adjustment:** the work is correctly divided already; only the AC boundary is drawn in the wrong place. Moving it costs four document edits, unblocks 6.5 honestly, and hands 6.2 a prerequisite that would otherwise have been discovered a third time.

### Timeline impact

**None to the critical path.** 6.5 closes now instead of waiting on infrastructure it cannot provision. 6.2 was already blocked on the serving decision and remains so — but it can now begin the local conversion and verification work immediately, which is new progress rather than new delay.

---

## 4. Detailed Change Proposals

### 4.1 — `epics.md` · Story 6.5 · Acceptance Criteria

**OLD**
```
**And** the resulting model is servable behind the contract `FineTunedModelProvider`
expects: `{acceptance_criteria, system_prompt, response_format}` in,
`BDDGenerateResponse`-shaped JSON out, within `FINE_TUNED_MODEL_TIMEOUT_SECONDS`
```

**NEW**
```
**And** a serving shim exists under `training/serve/` implementing the exact contract
`FineTunedModelProvider` sends — `{acceptance_criteria, system_prompt, response_format}`
in, `BDDGenerateResponse`-shaped JSON out — validated before responding and bounded
inside `FINE_TUNED_MODEL_TIMEOUT_SECONDS`, with tests proving the contract and the
output guarantee
**And** the chat template the run trained with is persisted beside the adapter so that
serving can be verified against it — *end-to-end serving of the trained model is
Story 6.2's, which owns the serving decision*
```

**Rationale:** 6.5 keeps everything it can own and prove — the contract, the validation, the budget, the template artifact. What it cannot own without 6.2's decision is deployment of a running model. The italicised clause names the owner so the boundary cannot be misread again.

---

### 4.2 — `epics.md` · Story 6.2 · Acceptance Criteria

**OLD**
```
**Given** a fine-tuned model is deployed and accessible via HTTP endpoint
...
**And** BDD generation via the fine-tuned model completes within 30 seconds for up to
20 AC clauses (NFR-P3)
```

**NEW**
```
**Given** Story 6.5's LoRA adapter exists at `training/outputs/outputs/bdd-lora/`
**When** it is converted to a servable artifact and exposed over HTTP
**Then** the `FineTunedModelProvider` sends the acceptance criteria to that endpoint and
parses the response into Gherkin scenarios

**And** the conversion from PEFT adapter to servable artifact is documented and
reproducible — Story 6.3 consumes the same artifact for local evaluation
**And** the shim reports `chat_template.status == "match"` on `/health`, proving serving
applies the template the model was trained with
**And** if the fine-tuned model endpoint is unavailable, the system falls back to
`GeneralLLMFallbackProvider` with a warning log
**And** a BDD generation request served by the fine-tuned model returns a valid
`BDDGenerateResponse` within `FINE_TUNED_MODEL_TIMEOUT_SECONDS` (**12s**, per
`config.py:43` — not the 30s previously written here; 12s is a deliberate sub-budget so
the general-LLM fallback still completes inside NFR-P3's 30s)
**And** the response format is identical to the general LLM output — no frontend changes
required
```

**Rationale:** three fixes in one edit. The precondition becomes something that actually exists; the unowned conversion gets an owner; and the timeout stops contradicting the code. The template check is carried over from 6.5's verification mechanism so the guarantee is not lost in the handoff.

---

### 4.3 — `epics.md` · Epic 6 preamble

**OLD**
```
> **Serving is an open decision.** Production is `t3.small` (no GPU) and cannot host a
> 7B model. Story 6.2 is blocked on a serving decision as well as on model availability.
> Story 6.3 can evaluate locally via the Ollama provider without resolving it.
```

**NEW**
```
> **Serving is an open decision, and Story 6.2 owns all of it.** Production is
> `t3.small` (no GPU) and cannot host a 7B model. Story 6.5 produces a LoRA adapter and
> a contract-complete shim; **converting that adapter into a servable artifact and
> proving an end-to-end request belongs to 6.2**, along with the production hosting
> decision. Story 6.3 evaluates locally via Ollama against the artifact 6.2 produces —
> it needs the conversion, not the production decision.
```

**Rationale:** the original note is what made the misplacement survive review — it says serving is 6.2's while 6.5's AC demanded servability. Naming conversion explicitly closes the gap that let it fall between the two.

---

### 4.4 — `architecture.md` · Deferred Decisions

**OLD**
```
- Fine-tuned model **serving** infrastructure — unresolved. Production is `t3.small`
  (no GPU) and cannot host a 7B model; Story 6.2 requires this decision.
```

**NEW**
```
- Fine-tuned model **serving** infrastructure — unresolved. Production is `t3.small`
  (no GPU) and cannot host a 7B model; Story 6.2 requires this decision. **A trained
  model now exists** (2026-08-09): a Qwen2.5-7B QLoRA adapter, eval loss 0.3976, at
  `training/outputs/outputs/bdd-lora/` (git-ignored) with the chat template it trained
  with. It is a PEFT adapter, not a servable endpoint — conversion is Story 6.2's.
```

**Rationale:** the deferral is still correct; what changed is that the input to the decision now exists. Recording where it lives prevents the third rediscovery.

---

### 4.5 — `6-5-finetuning-dataset-and-training.md` · AC6, Task 7, Status

- **AC6** — narrowed to match 4.1: shim contract + template persistence, not end-to-end serving.
- **Task 7** — the open subtask is replaced with a handoff line pointing at Story 6.2. It is **not** re-ticked; the work moves rather than disappearing.
- **Status** — `in-progress` → `done`.
- **Change Log** — an entry recording this proposal and what moved.

**Rationale:** the story file must match the epic, and the audit trail must show the subtask was reassigned, not quietly satisfied.

---

### 4.6 — `sprint-status.yaml`

**OLD**
```yaml
6-5-finetuning-dataset-and-training: in-progress  # …AC6 still partial…
6-2-fine-tuned-model-integration: backlog  # blocked: needs 6.5 AND a serving decision (t3.small has no GPU)
```

**NEW**
```yaml
6-5-finetuning-dataset-and-training: done  # trained 2026-08-09 on Kaggle T4, eval loss 0.3976; adapter at training/outputs/outputs/bdd-lora/. End-to-end serving verification moved to 6.2 per sprint-change-proposal-2026-08-09
6-2-fine-tuned-model-integration: backlog  # 6.5 dependency SATISFIED (adapter exists). Now owns: PEFT→servable conversion, end-to-end shim verification, and the production serving decision (t3.small has no GPU). Local conversion work can start now
```

**Rationale:** 6.2's blocker was compound and only half of it is still true. Recording which half unblocks real work.

---

## 5. Implementation Handoff

**Scope classification: MODERATE** — backlog reorganisation across two stories, no fundamental replan.

| Recipient | Responsibility |
|---|---|
| **Scrum Master / PO** (`bmad-bmm-sm`) | Apply 4.1–4.4 to `epics.md` and `architecture.md`; apply 4.6 to `sprint-status.yaml` |
| **Dev agent** | Apply 4.5 to the 6.5 story file |
| **Chamath** | Approve. Then decide whether 6.2's local conversion starts now or waits on the production hosting decision |

*(In practice I can apply all six edits in one pass on approval — the split above records accountability, not separate sittings.)*

### Success criteria

1. `epics.md` no longer asks 6.5 to prove servability, and 6.2's timeout reads 12s
2. The PEFT → servable conversion is named in exactly one story (6.2)
3. Story 6.5 is `done` with no open tasks and no un-evidenced claims
4. `sprint-status.yaml` distinguishes 6.2's satisfied dependency from its still-open one
5. The 6.5 Change Log records that the subtask moved, not that it was completed

### Not in scope

Choosing the production serving target (Modal, RunPod, a GPU instance, or abandoning fine-tuned serving for a general-LLM-only MVP). That decision stays open and stays 6.2's — this proposal only ensures 6.2 has everything it needs to make it.

---

## Checklist Record

| § | Item | Status |
|---|---|---|
| 1.1 | Triggering story identified | [x] Story 6.5, via code review 2026-08-09 |
| 1.2 | Core problem defined | [x] Misplaced AC — 6.5 required to prove what 6.2 gates |
| 1.3 | Evidence gathered | [x] Six items, §1 |
| 2.1 | Epic completable as planned? | [x] Yes, with the AC boundary corrected |
| 2.2 | Epic-level changes | [x] Modify AC scope in 6.5 and 6.2; no add/remove |
| 2.3 | Remaining epics reviewed | [x] Epics 1–5 `done`, unaffected |
| 2.4 | Epics invalidated or needed? | [x] None |
| 2.5 | Order/priority change? | [N/A] 6.2 already precedes 6.3 |
| 3.1 | PRD conflicts | [N/A] MVP unaffected; Epic 6 is Phase 2 |
| 3.2 | Architecture conflicts | [!] `:215-216` stale — addressed in 4.4 |
| 3.3 | UI/UX conflicts | [N/A] No UI surface |
| 3.4 | Other artifacts | [x] `sprint-status.yaml` (4.6); CI/deploy/tests unaffected |
| 4.1 | Option 1: Direct Adjustment | [x] **Viable** — Low effort, Low risk |
| 4.2 | Option 2: Rollback | [x] Not viable — nothing defective to revert |
| 4.3 | Option 3: MVP Review | [N/A] Epic 6 is outside MVP |
| 4.4 | Path selected | [x] **Option 1** |
| 5.1–5.5 | Proposal components | [x] §1–§5 above |
| 6.1–6.3 | Review and approval | [x] Approved by Chamath, 2026-08-09 |
| 6.4 | `sprint-status.yaml` updated | [x] 6-5 → `done`; 6-2 blocker split |
| 6.5 | Handoff confirmed | [x] All six edits applied in one pass |
