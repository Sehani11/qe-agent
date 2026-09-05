# training/

Fine-tuning support for Epic 6. **Nothing here runs in production.**

This directory lives at the repo root, outside `backend/`, on purpose: the
backend Dockerfile ends with `COPY . .`, so anything placed under `backend/`
ships in the runtime image. Training dependencies (torch, transformers, peft)
must **never** be added to `backend/pyproject.toml` — they would add gigabytes
to an image for a service that only ever performs inference.

## What exists today

| File | Purpose |
|---|---|
| `build_dataset.py` | Builds `(acceptance criteria → Gherkin)` training pairs |
| `config/train_config.yaml` | Every hyperparameter, in one place |
| `finetune_bdd.py` | The training code — plain transformers + peft + Trainer |
| `finetune_bdd.ipynb` | The same code as a notebook (generated from the script) |
| `kaggle_run.py` | Pushes the dataset + kernel to Kaggle and fetches results |
| **`KAGGLE.md`** | **Setup, credentials and every gotcha — read before your first run** |
| `serve/app.py` | HTTP shim implementing the `FineTunedModelProvider` contract |
| `CORPUS.md` | Corpus sources, licences, commit SHAs, scan output |
| `RUN_LOG.md` | Measured results of each training run |
| `requirements.txt` | Local tooling only — never `backend/pyproject.toml` |
| `outputs/run-<id>/` | Adapters produced by a run started from `/fine-tune` |

`data/` and `corpus/` are **git-ignored**: derived training data can contain
user-captured content that Stories 6.6/6.7 exist to govern, and cloned corpora
carry their own licences.

## The short path: run it from the app

`/fine-tune` in the app drives this same pipeline for a corpus supplied through
the upload box. **Train now** builds a set from the uploaded datasets, pushes it
to Kaggle, polls the kernel, and copies the adapter back to
`training/outputs/run-<id>/`, where a **Download model** button serves it as a
zip. Progress lands in the `training_runs` table, which is what the page polls.

Two things it needs, both checked before the build starts so a missing one does
not cost a corpus's worth of LLM calls:

* `training/` present on the server — the backend image deliberately does not
  ship it, so this works from a checkout, not from the container
* `KAGGLE_USERNAME` and `KAGGLE_KEY`, exactly as `KAGGLE.md` describes

The backend never imports anything here. `app/services/training_run_service.py`
launches `build_dataset.py` and `kaggle_run.py` as subprocesses against the repo
root, so their CLI and their stdout are the interface — changing an argument
name or the `Wrote N train / M holdout` summary line breaks it.

Only one run happens at a time, globally: `training/data`, the Kaggle dataset
slug and the kernel are all single shared resources.

Everything below is the same pipeline driven by hand, which is still the right
way to build from a cloned corpus or to change hyperparameters.

## The workflow, end to end

```bash
# 1. Acquire a corpus (see CORPUS.md for the exact repositories and licences)
mkdir -p training/corpus && cd training/corpus && git clone --depth 1 …

# 2. Check what survives the quality filters before spending anything
uv run --project backend python training/build_dataset.py \
    --features-dir training/corpus --dry-run

# 3. Build for real (one LLM call per document — try --limit first)
uv run --project backend python training/build_dataset.py \
    --features-dir training/corpus --out training/data

# 3b. Add the authored product-domain tickets. No LLM: both sides of these
#     pairs are written by hand in training/corpus-product/*.yaml, which is the
#     reviewable source of record. --pairs-dir also re-ingests an existing
#     training/data, so this merges without paying for back-generation twice.
uv run --project backend python training/build_product_pairs.py
uv run --project backend python training/build_dataset.py \
    --pairs-dir training/data-v1 --pairs-dir training/corpus-product \
    --out training/data

# 4. Train. See KAGGLE.md for setup and the gotchas — the short version is
#    "push code by API, start the run from the UI with a T4 accelerator":
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --push
#    ...then Save & Run All in the browser, then:
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --fetch
#    Paste the printed table into RUN_LOG.md. It carries the holdout quality
#    metrics (coverage, duplicate rate, reference alignment) as well as loss —
#    loss alone does not say whether the model is good at the task.

# 5. Serve the result — see serve/README.md
```

## ⚠️ The parser lives in the backend, not here

`parse_feature`, the quality thresholds and the JSONL validation live in
`backend/app/services/training_data_service.py`. This script imports them.

That is deliberate. The app's training-data upload page (Story 6.7) must accept
exactly what this builder keeps — if the two had separate rules, users would
upload files the builder silently discards and the corpus would look healthy
while being empty. That is not hypothetical: Story 6.6 found every `uploaded`
row in the live database failing the quality filter.

The dependency only points one way. `training/` sits outside the backend Docker
build context, so the backend can never import this script; this script already
puts `backend/` on `sys.path`. Shared rules therefore live in the backend.
**Do not re-add a local copy of the parser here.**

## The data problem this works around

A fine-tune needs paired input and output. The app currently stores neither
half cleanly:

- `sessions` records only `jira_ticket_id` — the acceptance criteria text that
  drove generation is **not persisted**
- BDD edits made in the editor are **never saved** (`api/v1/bdd.py` exposes only
  `/generate` and `/upload`)

So `build_dataset.py` works **backwards**: it takes Gherkin that already exists
and asks an LLM to reconstruct the acceptance criteria that would have produced
it. The side the model learns to *emit* is genuine human-authored Gherkin; only
the input side is synthetic.

This is a bootstrap, not a substitute for capturing real data. User corrections
— someone editing generated BDD — are the one signal no teacher model can
invent, because it encodes your team's taste. Persisting them remains the
highest-value change available.

## Usage

Parse and inspect a corpus without spending anything (no LLM, no DB):

```bash
uv run --project backend python training/build_dataset.py \
    --features-dir ./corpus --dry-run
```

Full build (uses `LLM_PROVIDER` and that vendor's key — `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` — from `backend/.env`):

```bash
uv run --project backend python training/build_dataset.py \
    --features-dir ./corpus --from-db --out training/data
```

Outputs `train.jsonl` and `holdout.jsonl` in chat format, with the system
message imported from `bdd_service.BDD_SYSTEM_PROMPT` so training and serving
cannot drift apart.

### Sourcing a corpus

Harvesting is deliberately **not** automated — licensing is a human decision.
Clone Cucumber / SpecFlow / Behat repositories with permissive licences into a
directory and point `--features-dir` at it. The parser walks `*.feature`
recursively, so a directory of clones works as-is.

`--from-db` reads `bdd_files`. `--db-source` selects which rows:

| Choice | Selects | Use it for |
|---|---|---|
| `uploaded` *(default)* | `source='uploaded'` | `.feature` files a person uploaded |
| `edited` | `source='edited'` | Human corrections of generated output |
| `authored` | `uploaded` + `edited` | Everything a person wrote — the usual choice |
| `generated` | `source='generated'` | Self-distillation; see below |
| `all` | every row | Diagnostics, not training |

**`authored` is the one that closes the loop from app usage to training.**
`edited` rows are the strongest signal the product collects: each is a human
saying exactly where the model was wrong and what the right answer looks like.
They were previously reachable only through `all`, which dragged every
`generated` row along with them — so the best data could not be used without
self-distillation riding in beside it.

`--db-source generated` is available but is pure self-distillation: training on
this app's own LLM output can only teach the student to imitate the teacher it
already calls at runtime.

The default stays `uploaded` so existing commands select exactly what they
always did.

### `--from-uploads`: the corpus supplied through the app

Story 6.7 added a training-data page where a researcher uploads their own
`.feature` files or `.jsonl` datasets. `--from-uploads` reads the
`training_datasets` table and downloads each object from Supabase Storage
(so `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` must be set). Restrict it to
one user with `--uploads-user <user_id>`; the default reads every user's.

```bash
uv run --project backend python training/build_dataset.py \
    --from-uploads --dry-run
```

**Uploaded `.jsonl` pairs skip back-generation.** They are already complete
`(user → assistant)` pairs, so running them through the LLM would throw away the
researcher's own input side and pay to invent a worse one. They are merged after
back-generation and before the split, carrying `meta.origin` set to the uploaded
file so all of one file's pairs stay on the same side of the holdout boundary.

Uploaded `.feature` files are treated like any other document: parsed, quality
filtered, then back-generated.

Opted-out rows are excluded here too — see below.

### Opted-out rows are always excluded

Every `bdd_files` **and** `training_datasets` row carries `training_opt_in`,
stamped from the `TRAINING_DATA_OPT_IN` environment variable **at the moment it
was written** (Stories 6.6 and 6.7). Rows flagged `false` are never included,
and the builder reports how many it skipped:

```
Corpus scan
  seen                   12
  opted out              4      ← excluded, not missing
```

That counter exists so an entirely opted-out database is distinguishable from an
empty one — otherwise both produce the same "no usable feature documents"
message and you debug the wrong problem.

Changing `TRAINING_DATA_OPT_IN` affects **future** rows only. It is deliberately
not read at build time: consent belongs to the moment of capture, so flipping the
variable must never reclassify data that already exists.

### `bdd_files.content` is not one format — the builder branches on `content_format`

Story 6.4 added a `content_format` column because the table has always held two
different things, and the builder now selects a parser per row:

| `source` | `content_format` | Holds | Parser |
|---|---|---|---|
| `generated` | `json` | A serialized `BDDGenerateResponse` | `feature_doc_from_json` |
| `uploaded` | `gherkin` | Raw `.feature` text | `parse_feature` |
| `edited` | `gherkin` | The user's corrected `.feature` text | `parse_feature` |

Before Story 6.5 added the JSON branch, `generated` rows parsed to nothing and
were miscounted as unparsable — 8 of the 10 rows in the live database. They now
parse, but remain **excluded from the default `--db-source uploaded`**: training
on this app's own LLM output is self-distillation, which can only teach the
student to imitate the teacher it already calls at runtime.

This asymmetry matters most for **preference pairs**. Story 6.4 links each
`edited` row to the `generated` row it corrects via `parent_id`, which is exactly
a (rejected, chosen) pair for DPO — but the two sides are stored in *different
formats*, so they must be normalized to the same representation before they can
be diffed or paired. The frontend's `scenariosToGherkin()` is the canonical
JSON→Gherkin rendering; port it rather than inventing a second one.

Edited rows also carry `acceptance_criteria`, inherited from their parent, so a
correction is a complete training pair on its own — no join required for plain
supervised fine-tuning.

## Quality filters

Public Gherkin is frequently poor. Documents are rejected for: missing any of
Given/When/Then, placeholder text (TODO/FIXME/TBD), steps outside 8–400
characters, more than 20 scenarios, files over 20k characters, and duplicate
content. `Scenario Outline` blocks are **expanded** against their Examples
table (capped at 3 rows each); an outline with no usable table is still skipped,
since its `<placeholders>` would otherwise reach the model verbatim. Run
`--dry-run` to see exactly what was discarded and why.

Two invariants worth keeping if you modify this:

1. **Every target is validated against `BDDGenerateResponse` before it is
   written**, and empty `scenarios` arrays are dropped. An empty array
   *validates successfully* (the field defaults to `[]`), so without that check
   the dataset would quietly teach the model to return nothing.
2. **The train/holdout split is by origin file, never by row.** Scenarios from
   one feature file are near-duplicates; splitting per row leaks them across the
   boundary and makes the holdout score meaningless.

## Gherkin the parser understands

`Feature:`, `Scenario:`, `Background:` (its steps are inherited by every scenario
in the file), `Scenario Outline:` with its `Examples:` table, and
`Given`/`When`/`Then`/`And`/`But`.

`Scenario Outline` expansion was added 2026-08-13. Each Examples row becomes a
concrete scenario with `<placeholders>` substituted, capped at
`MAX_OUTLINE_EXPANSIONS = 3` per outline — outlines exist to repeat the same
steps over many inputs, so expanding a 40-row table would emit 40 near-identical
scenarios and teach exactly the repetition the fine-tune already over-produces.
An outline whose table is missing or unusable is skipped and counted, because
unsubstituted placeholders would reach the model verbatim.

Measured effect on the corpus: **213 → 222 documents (+4%)**, **+77 scenarios**.
Modest, because `outlines skipped` counts outline *blocks*, and most sat inside
files already kept for their ordinary scenarios — only 9 files were rescued
outright. It also fixes a live upload bug: a user submitting an outline-based
`.feature` file was previously rejected.

Background support was added in Story 6.5 and matters more than it sounds: 38% of
real-world feature files declare their `Given` once in a Background, and without
it every one of those scenarios looked incomplete. Adding it more than doubled
the corpus kept from the same repositories. It also fixed a user-facing bug — a
valid `.feature` file using a Background was being rejected at upload.

## Not yet built

- Evaluation harness — Story 6.3 specifies `python -m app.evaluate_models`,
  which belongs in the backend, not here
- A trained model. `RUN_LOG.md` documents the run; executing it needs a human
  with a Kaggle or Colab account
