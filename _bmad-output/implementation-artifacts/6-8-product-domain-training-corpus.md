# Story 6.8: Product-Domain Training Corpus

Status: done

<!-- Retro-documented 2026-08-22. This story was implemented outside the BMAD
     workflow on 2026-08-16/17; the acceptance criteria below were reconstructed
     from training/RUN_LOG.md, training/CORPUS.md and the commit history, not
     drafted before the work. See the Change Log. -->

## Story

As a **researcher**,
I want a training corpus written in the **product's own domain** rather than testing-framework Gherkin,
so that the fine-tune learns to reason about behaviour instead of learning the shape of `rspec` invocations.

## Acceptance Criteria

1. **Given** `RUN_LOG.md` has recorded the same conclusion since Run 1 — the model learned the *shape* of the task and not the reasoning, because 84% of the corpus is Gherkin describing CLI invocations — and **given** Run 2 established that more data of the same kind changes nothing
   **When** product-domain pairs are authored
   **Then** **both sides** of every pair (the acceptance criteria *and* the Gherkin) are written by a human in reviewable YAML under `training/corpus-product/`, with **no LLM involved** — an LLM-written target would train the fine-tune to imitate the general model it is supposed to beat, capping it at the baseline's quality by construction

2. **And** `training/build_product_pairs.py` compiles those YAML files into `<name>.jsonl` in exactly the shape `build_dataset.py --pairs-dir` consumes, validating every target against `BDDGenerateResponse` and embedding the same `BDD_SYSTEM_PROMPT` the serving path sends — a pair whose target the serving schema rejects is a pair the model is being taught to emit invalidly

3. **And** `build_dataset.py` accepts `--pairs-dir` so an existing dataset can be re-ingested and merged **without paying for back-generation twice**, and the pre-merge dataset is preserved at `training/data-v1/` so the two are separable after the fact

4. **And** a `--check` mode validates the YAML without writing anything, so an authoring error is caught before it reaches a training run

5. **And** the merged dataset is trained and the result recorded in `RUN_LOG.md` with the **holdout split by domain group** — an aggregate over a holdout that is still ~85% legacy corpus will hide the effect of the new data entirely

6. **And** the result is reported with its sample size stated plainly: three product-domain holdout items is a **signal, not a result**, and must not be written up as one

## Context & Critical Background

### Why this is not `build_dataset.py`

```
build_dataset.py     real .feature file → LLM invents the AC → pair
build_product_pairs  authored ticket AND scenarios → pair, no LLM at all
```

The distinction is the whole point of the story. `build_dataset.py` back-generates acceptance criteria from existing Gherkin, which is how 226 pairs were assembled cheaply — but it can only produce pairs in the domain of the corpus it reads, and that corpus is testing-framework Gherkin. No amount of back-generation escapes the domain.

### The corpus

| File | Pairs | Domain |
|---|---|---|
| `training/corpus-product/resume-generator.yaml` | 20 | Resume builder — document generation, templates, export |
| `training/corpus-product/user-management.yaml` | 8 | User management — roles, invitations, deactivation |
| **Total** | **28** | 12.4% of dataset v2 (226 pairs) |

### Reuse map

| Piece | Location |
|---|---|
| Target validation | `app.schemas.bdd.BDDGenerateResponse` |
| System prompt embedded in each pair | `app.services.bdd_service.BDD_SYSTEM_PROMPT` |
| Merge path | `build_dataset.py --pairs-dir` (accepts this script's output *and* `training/data-v1`) |
| Training run | `training/finetune_bdd.py` + `config/train_config.yaml`, unchanged |

## Tasks / Subtasks

- [x] **Task 1: Author `corpus-product/*.yaml`** (AC: 1) — 28 tickets across two product domains, AC and Gherkin both hand-written and reviewable as prose
- [x] **Task 2: `training/build_product_pairs.py`** (AC: 2, 4)
  - [x] Read YAML → emit `<name>.jsonl` beside each source file
  - [x] Validate every target against `BDDGenerateResponse`; embed `BDD_SYSTEM_PROMPT`
  - [x] `--check` mode: validate without writing
  - [x] `os.chdir` to `backend/` before importing `app.core.config`, matching `build_dataset.py` — the repo-root `.env` carries keys `Settings` would otherwise reject and print
- [x] **Task 3: `build_dataset.py --pairs-dir`** (AC: 3) — ingest complete pairs, skipping back-generation; preserve the prior dataset as `training/data-v1/`
- [x] **Task 4: Train on the merged dataset** (AC: 5) — Run 5, config identical to Runs 3/4 so the dataset is the only changed variable
- [x] **Task 5: Record the result with the holdout split by domain** (AC: 5, 6) — `RUN_LOG.md` Run 5

## Dev Notes

### Runs 4 and 5

Run 4 exists to give Run 5 a baseline. Its config is **identical to Run 3** — same 1.5B base, same 179/19 split, same seed, learning rate, epochs, batch size, LoRA rank and sequence length. Nothing was tuned. The only change was that the run began scoring the whole holdout on the GPU before the session ended, so quality metrics stopped depending on the ~62s/item local serving path.

| | Run 4 (2026-08-16) | Run 5 (2026-08-17) |
|---|---|---|
| Dataset | 179 train / 19 holdout | **204 train / 22 holdout** (v2, +28 product) |
| Final train loss | 0.5007 | 0.5621 |
| Final eval loss | 0.5686 | **0.5542** |
| Holdout items scored | 18 of 19 | 20 of 22 |
| JSON parse rate | 0.9474 | 0.9091 |
| AC-clause coverage | 1.0000 | 0.980 |
| Duplicate rate (aggregate) | 0.3222 | 0.3629 |
| Scenarios per item | 2.1111 | 3.00 |
| Wall clock (train) | 7.2 min | 8.8 min |

### The finding, and why the aggregate is wrong about it

Run 5's aggregate duplicate rate (0.3629 vs Run 4's 0.3222) reads as a regression. **Split the holdout by domain and it inverts:**

| Holdout group | Items | Duplicate rate | Coverage | Scenarios/item |
|---|---|---|---|---|
| Legacy testing-framework | 17 | **0.407** | 1.000 | 2.88 |
| **Product-domain (new)** | 3 | **0.111** | 0.867 | 3.67 |

Repetition is **3.7× lower on the domain the new data covers**. Two of the three product items produced four scenarios with zero duplicates — something no item in Run 4 managed at any length. This is the first direct evidence that **corpus domain**, not model capacity and not dataset size, drives the repetition that `RUN_LOG` has been blaming on the corpus since Run 1 without being able to measure it.

The aggregate hides it because 17 of 20 scored items are still legacy corpus.

### What got worse, recorded honestly

- **Coverage fell 1.000 → 0.980.** The single miss is on a product item: RG-002 declares five criteria and the model covered three — the weakest coverage in the run, on the domain that is otherwise its best.
- **Reference alignment is lower on product items** (0.244) than legacy ones (0.330). Alignment is crude lexical overlap, and the legacy references share heavy boilerplate ("I run `rspec`") that is trivially easy to match. A weak signal here, not a quality verdict.

### Do not over-read three items

Three product-domain holdout items is a strong signal on a tiny sample. Turning it into a result needs more product-domain pairs **and** a holdout that is not 85% off-domain. That is the next increment, not this one.

### Project Structure Notes

**Created:** `training/build_product_pairs.py`, `training/corpus-product/resume-generator.yaml`, `training/corpus-product/user-management.yaml`, `training/data-v1/{train,holdout}.jsonl` (git-ignored).
**Modified:** `training/build_dataset.py` (`--pairs-dir`), `training/README.md`, `training/CORPUS.md`, `training/RUN_LOG.md`, `training/config/train_config.yaml`.
**Do NOT modify:** `backend/pyproject.toml` — training dependencies must never enter the image the backend ships.

### References

- [Source: training/RUN_LOG.md] — Runs 4 and 5, including the domain-split holdout table
- [Source: training/CORPUS.md] — corpus sources, licences and scan output
- [Source: _bmad-output/implementation-artifacts/6-5-finetuning-dataset-and-training.md] — the original corpus acquisition this extends
- [Source: _bmad-output/implementation-artifacts/6-3-model-evaluation-pipeline.md] — the 2026-08-13 conclusion that sharpened the remedy from "better data" to "Gherkin that resembles the product's domain"

## Dev Agent Record

### Completion Notes List

- **No LLM in the loop (AC1).** Both sides of all 28 pairs are authored in YAML and reviewable as prose before they become JSON.
- **Contract-shaped output (AC2).** Every target validates against `BDDGenerateResponse`; the pairs carry `BDD_SYSTEM_PROMPT` so training and serving see the same instruction.
- **Merge without re-spending (AC3).** `--pairs-dir` re-ingests `training/data-v1` alongside `corpus-product`, so the 198 existing pairs were not back-generated a second time.
- **Dataset is the only changed variable (AC5).** Run 5 holds every hyperparameter from Runs 3/4 fixed on purpose; any difference is attributable to the data.
- **The measurement that mattered was the split, not the aggregate.** Reporting only the aggregate would have recorded this run as a regression.

### File List

**Training — created:**
- `training/build_product_pairs.py` — YAML → JSONL compiler, no LLM
- `training/corpus-product/resume-generator.yaml` — 20 authored pairs
- `training/corpus-product/user-management.yaml` — 8 authored pairs

**Training — modified:**
- `training/build_dataset.py` — `--pairs-dir` ingestion/merge
- `training/config/train_config.yaml` — dataset v2 paths
- `training/RUN_LOG.md` — Runs 4 and 5
- `training/README.md`, `training/CORPUS.md` — workflow and provenance

## Change Log

- 2026-08-22: **Retro-documented.** The work landed on 2026-08-16/17 outside the BMAD workflow, so this file was written after the fact from `RUN_LOG.md`, `CORPUS.md` and the commit history rather than drafted ahead of implementation. The acceptance criteria are a reconstruction of what the work actually had to satisfy; they were not agreed in advance, and this note exists so no one later reads them as if they were.
- 2026-08-17: **Run 5 — first run on the product-domain dataset.** 28 authored product tickets merged into the corpus (226 pairs, 12.4% product). Eval loss 0.5542 against Run 4's 0.5686 with an identical config. The result is in the domain split, not the aggregate: duplicate rate **0.111 on product-domain holdout items vs 0.407 on legacy** — 3.7× lower repetition on the domain the new data covers, and the first direct evidence that corpus *domain* drives the repetition rather than model capacity or dataset size. Coverage fell 1.000 → 0.980 on one product item (RG-002, 3 of 5 criteria). Three items is a signal, not a result.
- 2026-08-16: **Run 4 — baseline for the dataset change.** Config identical to Run 3; the only change is in-process holdout scoring on the GPU, which removed the ~62s/item local serving path from the quality measurement. Eval loss 0.5686, coverage 1.0000, duplicate rate 0.3222 — the numbers Run 5 is measured against.
