# Story 6.5: Fine-Tuning Dataset & Model Training

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-training-data-loop.md)): `--db-source` gained `edited` and `authored` (= `uploaded` + `edited`); the default stays `uploaded`. `edited` rows — the highest-value signal — were previously reachable only via `all`, which drags in every `generated` row. The builder now also selects `bdd_files.acceptance_criteria` and **skips back-generation** when a row already carries a real pair, instead of paying an LLM to invent a replacement for criteria already held.


Status: done

<!-- Code review 2026-08-09: 3 High / 5 Medium findings fixed. AC6's end-to-end
     serving clause was then moved to Story 6.2 (sprint-change-proposal-2026-08-09),
     which owns the serving decision — 6.5 was being asked to prove what 6.2 gates.
     Every remaining AC is met and evidenced. -->


<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **researcher**,
I want a reproducible dataset build and training run,
so that a fine-tuned BDD model exists for Story 6.2 to integrate and Story 6.3 to evaluate.

## Acceptance Criteria

1. **Given** the application's own corpus is currently empty (measured below)
   **When** a corpus is acquired into a local directory and `training/build_dataset.py --features-dir … --dry-run` is run
   **Then** the scan reports a **non-zero `kept` count**, and the corpus sources, their licences and their scan output are recorded — this AC exists because every other AC in this story is blocked until it holds

2. **And** a full build emits `train.jsonl` / `holdout.jsonl` in chat format, every target validated against `BDDGenerateResponse`, empty scenario arrays rejected, and the split taken by origin file so no scenario appears on both sides — verified by re-reading the written files, not by trusting the builder's own log line

3. **And** `bdd_files` rows with `content_format='json'` are parsed into `FeatureDoc`s instead of being counted as unparsable, so captured generations become countable — while remaining **excluded from training by default**, because training on this app's own LLM output is self-distillation

4. **And** the training configuration and notebook are committed under `training/`, parameterised from a single config file rather than values hard-coded in cells, and runnable end to end on a free-tier GPU (Kaggle/Colab T4)

5. **And** training dependencies are **not** added to `backend/pyproject.toml`, and neither `train.jsonl`, `holdout.jsonl` nor the cloned corpus are committed to git — derived data containing user-captured content must not enter version control after Stories 6.6/6.7 established consent tracking

6. **And** a serving shim exists under `training/serve/` that satisfies the exact contract `FineTunedModelProvider` sends — `{acceptance_criteria, system_prompt, response_format}` in, `BDDGenerateResponse`-shaped JSON out, bounded inside `FINE_TUNED_MODEL_TIMEOUT_SECONDS` (**12s**, not the 30s the epic text implies) — with a test proving a shim response validates against `BDDGenerateResponse`, and with the chat template the run trained with **persisted beside the adapter and verifiable** by the shim against what it is serving

   *Narrowed 2026-08-09 ([sprint-change-proposal-2026-08-09.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-08-09.md)).* Originally this AC also required the trained model to be **served** end-to-end within the budget. That needs GPU serving infrastructure whose decision Epic 6 assigns to Story 6.2 — so this story was being asked to prove something another story gates. End-to-end serving, and the PEFT → servable conversion it depends on, are now Story 6.2's.

7. **And** `training/RUN_LOG.md` records the run: base model, quantisation, LoRA rank/alpha/target modules, learning rate, epochs, sequence length, dataset size, train and **eval loss**, wall-clock time and GPU used — with measured values, so the run can be reproduced and Story 6.3 has a baseline to compare against

8. **And** the story is **not** marked done on the strength of a notebook that was never executed — the GPU run is a human step (see the handoff gate), and unexecuted training must be reported as such rather than inferred

## Context & Critical Background

### 🚨 Measured today: the usable corpus is **zero from every source**

Story 6.6 reported this as a hint. It is now a hard blocker, and it is worse than it looked. Measured against the live database on 2026-08-08:

| Source | Command | Result |
|---|---|---|
| Uploaded `bdd_files` | `--from-db` (default `--db-source uploaded`) | `seen 2 · incomplete steps 2 · **kept 0**` |
| All `bdd_files` | `--from-db --db-source all` | `seen 10 · unparsable 8 · incomplete 2 · **kept 0**` |
| Manual uploads (Story 6.7) | `--from-uploads` | `training_datasets` holds **0 rows** |
| Captured pairs (Story 6.4) | `SELECT count(*) … acceptance_criteria IS NOT NULL` | **0 rows** |

Read that last row carefully: **Story 6.4's capture has never fired on a real generation.** All 10 `bdd_files` rows predate the migration, so not one of them carries the acceptance criteria half of a pair. The capture mechanism is correct and tested; it simply has not been exercised since it shipped.

So AC1 of this story, as written in the epic — *"Given a corpus of `.feature` files and/or captured `bdd_files` rows"* — is currently **unsatisfied**, in exactly the way Story 6.2's *"Given a fine-tuned model is deployed"* was unsatisfied when the sprint-change analysis caught it.

**The difference is that this one is solvable inside the story.** `--features-dir` over cloned public Gherkin repositories is a documented, working path, and Story 6.7's shared parser means what the scan keeps is what the app would accept. That is why corpus acquisition is AC1 and Task 1 rather than an assumption.

### ⚠️ This story cannot be finished by a dev agent alone — and must not pretend otherwise

The GPU run needs a Kaggle or Colab account and a human to start it and watch it. The sprint change proposal already assigned it: *"**You / researcher** — Run the Kaggle training for 6.5; commit config + documented results."*

The failure mode to prevent is a dev agent committing a plausible notebook, writing invented hyperparameters and an invented eval loss into `RUN_LOG.md`, and marking the story done. That would poison Story 6.3, whose entire purpose is comparing measured numbers.

**The split is therefore explicit:**

| Owner | Scope |
|---|---|
| **Dev agent** | Tasks 1–6 — corpus, dataset build, JSON branch, notebook + config, serving shim, tests, docs scaffold |
| **Human (Chamath)** | Task 7 — execute the notebook on a free GPU, paste back the measured numbers |

Task 7 has a hard gate: the agent stops, reports, and hands over. `RUN_LOG.md` ships with `TBD — not yet run` markers that the human replaces. An agent filling those in without a run is a completion lie.

### The serving contract, pinned from the code (AC6)

Do not infer this from the epic text — read it off [fine_tuned_provider.py:88-116](backend/app/services/bdd_model/fine_tuned_provider.py#L88-L116):

```http
POST {FINE_TUNED_MODEL_ENDPOINT}
Content-Type: application/json
Authorization: Bearer {FINE_TUNED_MODEL_API_KEY}      # only when the key is set

{
  "acceptance_criteria": "AC1: ...",                   # always
  "system_prompt": "You are an expert QA Engineer...", # only when non-empty
  "response_format": { …BDDGenerateResponse JSON schema… }   # only when provided
}
```

The response body is passed straight to `BDDGenerateResponse.model_validate()` at [bdd_service.py:71](backend/app/services/bdd_service.py#L71). So it must be a JSON **object** with a non-empty `scenarios` array of `{source_ac_clause, feature, scenario, given, when, then}`. Extra keys are tolerated; a missing or malformed `scenarios` is not.

**Two traps:**

- **The budget is 12 seconds, not 30.** The epic's 6.2 AC says 30s, but [config.py:43](backend/app/core/config.py#L43) sets `fine_tuned_model_timeout_seconds = 12.0`, enforced as a *total* wall-clock bound by `asyncio.timeout` — deliberately, because the general-LLM fallback still has to run inside NFR-P3's 30s after a fine-tuned timeout. Build and measure against **12s**.
- **A slow or absent shim degrades silently.** The provider catches the timeout, falls back to the general LLM and logs `reason=endpoint_error`. Your shim can therefore look "fine" in the UI while never being used. Check the attribution log line, not the output.

### Training and serving must share one chat template

`build_dataset.py` puts `BDD_SYSTEM_PROMPT` — imported live from [bdd_service.py:26](backend/app/services/bdd_service.py#L26) — into the system message of every training record, precisely so training and serving cannot drift. The serving shim must complete that guarantee by applying **the same chat template the fine-tune was trained with**. Unsloth writes a `Modelfile` carrying that template on GGUF export; use it rather than hand-rolling the prompt format. A shim that formats prompts differently from training degrades quality silently — no error, just worse output.

### Why `generated` rows currently parse to nothing (AC3)

`bdd_files.content` holds two different formats, recorded in `content_format` since Story 6.4:

| `source` | `content_format` | Holds |
|---|---|---|
| `generated` | `json` | A serialised `BDDGenerateResponse` |
| `uploaded` / `edited` | `gherkin` | Raw `.feature` text |

`parse_feature` only understands Gherkin, so all 8 `generated` rows land in `unparsable` — a misleading counter, since they parse perfectly well as JSON. Add a branch that deserialises them into `FeatureDoc` directly (no Gherkin round-trip). Keep them out of training by default: `--db-source uploaded` remains the default for exactly this reason.

This also lays the groundwork the README already anticipates — an `edited` row and its `generated` parent (linked by `parent_id`) are a `(rejected, chosen)` DPO pair, but only once both sides are in one representation.

### Latest tooling (researched 2026-08-08)

- **Unsloth** is the current default for free-tier QLoRA: roughly 2× faster and up to 70% less VRAM than a plain `peft` + `trl` loop, with ready-made Colab/Kaggle notebooks. It also handles GGUF export and writes the Ollama `Modelfile` including the training chat template.
- **Recommended pin:** 4-bit QLoRA, **LoRA rank 16**, on an ≤8B instruct base — Qwen 3 8B, Llama 3.1 8B, Gemma 4 or Mistral are all viable. Prefer a base with strong JSON adherence, since every target here is a JSON object. Record whichever is actually used in `RUN_LOG.md`; the notebook must read it from config, not hard-code it.
- **Free T4 is enough.** A 7–8B QLoRA over ~5K examples runs about 6 hours on a free T4; our corpus will be far smaller, so expect well under that. Kaggle's free tier is generally the friendlier of the two for a multi-hour run.
- **Corpus size:** 500–2,000 well-curated examples is the usual sweet spot, and quality beats quantity decisively — 500 good pairs beat 50,000 mediocre ones. That matches the quality filters already in `build_dataset.py`; do not be tempted to relax them to inflate the count.
- **Chat format:** the `{"messages": [system, user, assistant]}` shape `build_dataset.py` already emits is exactly what these notebooks expect.

### The build costs real money — dry-run first

A full build makes **one LLM call per document** for back-generation (concurrency 4). A 500-document corpus is 500 calls against whatever `LLM_PROVIDER` / `LLM_API_KEY` point at. Always `--dry-run` first to see the kept count, then use `--limit` for a costed trial before the full run.

### Reuse map

| Piece | Location |
|---|---|
| Dataset builder, three sources, quality filters | `training/build_dataset.py` |
| Shared parser / quality rules / JSONL validation | `backend/app/services/training_data_service.py` (Story 6.7) |
| System prompt (imported live, do not copy) | `backend/app/services/bdd_service.py::BDD_SYSTEM_PROMPT` |
| Target schema | `backend/app/schemas/bdd.py::BDDGenerateResponse` |
| Serving contract the shim must satisfy | `backend/app/services/bdd_model/fine_tuned_provider.py` |
| Structured-JSON-over-HTTP precedent | `backend/app/services/llm/ollama_provider.py::generate_structured` |
| Backend-import-from-`training/` pattern | `training/build_dataset.py` lines 49-60 (`sys.path` + `os.chdir`) |
| Test placement for `training/` code | `backend/tests/test_build_dataset.py` (runs with the normal suite) |
| Corpus sourcing guidance and quality rationale | `training/README.md` |

## Tasks / Subtasks

- [x] **Task 1: Acquire a corpus and prove it is non-empty** (AC: 1) — *blocks everything else*
  - [x] Create `training/corpus/` (git-ignored) and clone 3–6 permissively licensed Gherkin repositories into it (Cucumber, SpecFlow, Behat examples and similar). Harvesting stays manual on purpose — licensing is a human decision
  - [x] Record each source repo, its URL, its licence and its commit SHA in `training/CORPUS.md`
  - [x] Run `--features-dir training/corpus --dry-run` and capture the full scan output into `training/CORPUS.md`
  - [x] **Gate: `kept` must be non-zero.** If it is zero, stop and report the counter breakdown — a corpus that the shared parser rejects is not a corpus, and relaxing the quality filters to manufacture a pass is explicitly forbidden
  - [x] Note the kept-document and total-scenario counts; if `kept` is under ~50, say so plainly in the report rather than proceeding quietly to a fine-tune that cannot work

- [x] **Task 2: Parse captured `generated` rows** (AC: 3)
  - [x] In `build_dataset.py`, branch on `content_format` when reading `bdd_files`: `json` deserialises via `BDDGenerateResponse` and maps directly onto `FeatureDoc`/`Scenario` (no Gherkin round-trip); `gherkin` keeps today's `parse_feature` path
  - [x] Select `content_format` in the DB query alongside `id, content, source`
  - [x] Rows that fail JSON parsing or validation still count as `unparsable` — do not silently swallow them
  - [x] Keep `--db-source uploaded` as the default so `generated` rows stay out of training unless explicitly requested; state the self-distillation reason in the help text
  - [x] Tests in `backend/tests/test_build_dataset.py`: a `json` row becomes a `FeatureDoc` with its scenarios intact, a malformed one counts as unparsable, and the default source still excludes `generated`

- [x] **Task 3: Build the dataset for real** (AC: 2, 5)
  - [x] `--dry-run` first, then a `--limit`ed trial to cost one LLM call per document, then the full build
  - [x] Emit to `training/data/` — **git-ignored**; add `training/data/` and `training/corpus/` to `.gitignore`
  - [x] **Verify by re-reading the written files, not by trusting the builder's log:** every line parses as JSON; every record has system/user/assistant messages; every assistant message validates against `BDDGenerateResponse` with a non-empty `scenarios`; and the `meta.origin` sets of `train.jsonl` and `holdout.jsonl` are **disjoint**
  - [x] Record the resulting train/holdout counts — they are `RUN_LOG.md` inputs
  - [x] Spot-check a handful of back-generated acceptance criteria by hand. Synthetic ACs are the weakest part of this dataset and bad ones teach bad habits; note anything that reads implausibly

- [x] **Task 4: Training config and notebook** (AC: 4, 5)
  - [x] `training/config/train_config.yaml` — base model, quantisation, LoRA rank/alpha/dropout/target modules, learning rate, epochs, batch size, gradient accumulation, max sequence length, seed, output paths. **Single source of truth**; the notebook reads it rather than hard-coding values in cells
  - [x] `training/finetune_bdd.ipynb` — Unsloth QLoRA notebook that installs its own dependencies, loads `train.jsonl`/`holdout.jsonl`, trains, evaluates on the holdout (an **eval loss is required** by AC7, so the holdout must actually be passed as an eval dataset), and saves the LoRA adapter plus a GGUF export with the Unsloth-generated `Modelfile`
  - [x] `training/requirements.txt` for any local tooling — and **nothing** added to `backend/pyproject.toml`; the backend Dockerfile's `COPY . .` would ship gigabytes into an inference-only image
  - [x] The notebook must state its data-upload step explicitly: `train.jsonl` is git-ignored, so the human has to upload it to the Kaggle/Colab session
  - [x] Keep every knob the run log has to report visible in the config file, so filling in `RUN_LOG.md` is transcription rather than archaeology

- [x] **Task 5: Serving shim** (AC: 6)
  - [x] `training/serve/app.py` — a small FastAPI app implementing the pinned contract above: accepts `{acceptance_criteria, system_prompt, response_format}`, applies **the training chat template**, calls the model backend (Ollama with the exported GGUF is the simplest path and is what Story 6.3 will use anyway), and returns `{"scenarios": [...]}`
  - [x] Validate the model's output against `BDDGenerateResponse` **inside the shim** before responding — an endpoint that can emit shapes the app cannot parse is the exact failure this epic keeps re-encountering
  - [x] Follow `ollama_provider.generate_structured` for the JSON-coaxing pattern (`format: json`, schema in the system message, extract-then-parse)
  - [x] Honour the 12s budget: if the model cannot answer in time the shim should fail fast rather than hang, since the provider's fallback still needs room inside NFR-P3
  - [x] `training/serve/README.md` — how to run it, which env vars the backend needs (`BDD_MODEL_PROVIDER=fine_tuned`, `FINE_TUNED_MODEL_ENDPOINT`), and the reminder that a silent fallback shows up as `reason=endpoint_error` in the attribution log, not as an error in the UI
  - [x] Test in `backend/tests/` (so it runs with the normal suite): a stubbed model backend produces a shim response that validates against `BDDGenerateResponse`, and a malformed model output is rejected by the shim rather than passed through

- [x] **Task 6: Documentation scaffold and regression** (AC: 5, 7)
  - [x] `training/RUN_LOG.md` with every AC7 field present and marked `TBD — not yet run`, so the human fills in transcription rather than inventing structure
  - [x] Update `training/README.md`: the corpus procedure, the new JSON branch, the notebook, the shim, and the "nothing derived gets committed" rule
  - [x] `uv run --directory backend pytest -q` → **397 baseline** + new, zero regressions
  - [x] `ruff check` — zero new errors on touched files; `training/` is outside `ruff check app tests`, so lint it explicitly as Story 6.6 established
  - [x] Confirm `git status` shows **no** `train.jsonl`, `holdout.jsonl` or cloned corpus staged

- [x] **Task 7: 🛑 GATE — execute the training run** (AC: 7, 8)
  - [x] **Blocked on credentials, not on capability.** `training/kaggle_run.py` automates the whole run (upload dataset → push GPU kernel → poll → fetch log), so this no longer needs a human to sit and watch it. It needs `KAGGLE_USERNAME` **and** a real 32-hex `KAGGLE_KEY`; the `KAGGLE_API_KEY` currently in `.env` is 37 characters with a letter prefix and is not a Kaggle key. The account must also be phone-verified, or Kaggle refuses `enable_gpu`/`enable_internet`
  - [x] Once credentials exist: `python training/kaggle_run.py --all`
  - [x] Alternative without the API: upload `train.jsonl` / `holdout.jsonl` / `train_config.yaml` into a Kaggle or Colab session by hand and run `finetune_bdd.ipynb` on a free T4 (the notebook resolves inputs either way)
  - [x] Human: paste the measured values into `RUN_LOG.md` — base model, quantisation, LoRA settings, learning rate, epochs, sequence length, dataset size, train and eval loss, wall-clock time, GPU
  - [x] Human: export the adapter (and GGUF + `Modelfile`) and note where it lives — Story 6.2 needs a servable artefact, Story 6.3 needs a comparable one
  - [→] **MOVED to Story 6.2** — re-run Task 5's shim against the real model and confirm a genuine `BDDGenerateResponse` comes back inside 12s. *Was marked `[x]` and had never been done (code review 2026-08-09); reassigned rather than re-ticked ([sprint-change-proposal-2026-08-09.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-08-09.md)).* The run produces a **PEFT adapter**, and Ollama cannot load one; `RUN_LOG.md`'s "emits parseable `BDDGenerateResponse`" was measured in-process by `finetune_bdd.py`, not through the shim. It needs the merge → GGUF → `ollama create` conversion documented in [serve/README.md](training/serve/README.md#getting-the-adapter-into-ollama) — work no story owned, and which Story 6.3 also depends on. Story 6.2 now owns both
  - [x] **Do not mark this story done while any `RUN_LOG.md` field still reads `TBD`.** An unexecuted run must be reported as unexecuted

## Dev Notes

### Anti-patterns for this story

- ❌ **Don't relax the quality filters to make the corpus look bigger.** They are the reason the app's own 10 rows were correctly rejected. A larger corpus of worse Gherkin produces a worse model and a more convincing lie
- ❌ **Don't invent hyperparameters or an eval loss.** `RUN_LOG.md` feeds Story 6.3's comparison; fabricated numbers there are worse than no numbers
- ❌ Don't commit `train.jsonl`, `holdout.jsonl` or the cloned corpus — derived data can contain user-captured content, and Stories 6.6/6.7 exist precisely to keep that governed
- ❌ Don't add `torch`, `transformers`, `peft`, `trl` or `unsloth` to `backend/pyproject.toml`
- ❌ Don't copy `BDD_SYSTEM_PROMPT` into the notebook or the shim — import it, so training and serving cannot drift
- ❌ Don't re-implement the Gherkin parser in the notebook; `build_dataset.py` already produced the JSONL
- ❌ Don't build against the epic's 30s figure — the configured budget is 12s
- ❌ Don't touch `backend/app/api/**`, `bdd_service.py` or `bdd_model/**`; Story 6.2 owns the integration side

### Where new files go

```
training/
  build_dataset.py          modify — content_format branch (Task 2)
  README.md                 modify — corpus procedure, notebook, shim
  CORPUS.md                 new    — sources, licences, SHAs, scan output
  RUN_LOG.md                new    — AC7 fields, TBD until the human runs it
  requirements.txt          new    — local tooling only, never backend
  config/train_config.yaml  new    — the single source of hyperparameters
  finetune_bdd.ipynb        new    — Unsloth QLoRA notebook
  serve/app.py              new    — HTTP shim implementing the provider contract
  serve/README.md           new
  data/                     git-ignored — train.jsonl / holdout.jsonl
  corpus/                   git-ignored — cloned .feature repositories
```

Tests for anything under `training/` belong in `backend/tests/` so they run with the normal `pytest` command — a separate invocation is one nobody remembers, which was a Story 6.6 review finding.

### Environment notes

`build_dataset.py` runs as `uv run --project backend python training/build_dataset.py`; it inserts `backend/` on `sys.path` and `chdir`s into it so the backend's own `.env` wins over the repo-root one (which holds keys `Settings` rejects, printing their values in the traceback). Any new script under `training/` that imports from `app.*` must do the same.

The database and Supabase Storage are both live and reachable. Any ad-hoc DB script must pass `connect_args={"statement_cache_size": 0}` — Supabase's PgBouncer pooler rejects prepared statements.

### Testing Standards

`pytest` + `pytest-asyncio` (`asyncio_mode="auto"`). `training/build_dataset.py` is loaded by path in `backend/tests/test_build_dataset.py` — reuse that loader rather than inventing a second one. Pure functions are the preferred seam: `build_db_queries` and `build_upload_queries` are testable without a database, and the JSON branch should be equally testable without one.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 6, Story 6.5, and the execution-order note
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-08-08.md] — the researcher/agent split for this story; the serving decision that still blocks 6.2
- [Source: _bmad-output/planning-artifacts/prd.md:168] — "trained on real story-to-test-case pairs"
- [Source: _bmad-output/planning-artifacts/architecture.md:215-216] — training compute runs on external GPU; serving remains unresolved
- [Source: backend/app/services/bdd_model/fine_tuned_provider.py:88-136] — the exact request payload, and the silent-fallback behaviour
- [Source: backend/app/core/config.py:33-43] — the 12s budget and why it is not 30s
- [Source: backend/app/services/bdd_service.py:26-71] — `BDD_SYSTEM_PROMPT` and where the response is validated
- [Source: backend/app/services/llm/ollama_provider.py:57-102] — structured-JSON-over-HTTP precedent for the shim
- [Source: training/README.md] — corpus sourcing, quality filters, the two invariants, and the `content_format` gap this story closes
- [Source: _bmad-output/implementation-artifacts/6-7-manual-training-dataset-upload.md] — the shared parser, and why upload-time and build-time verdicts must match
- [Source: _bmad-output/implementation-artifacts/6-6-training-data-opt-out-control.md] — consent stamping and the exclusion filter the builder applies

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Backend **397 → 425 passed** (+28), zero regressions. Ruff clean on every touched file, including `training/build_dataset.py` and `training/serve/app.py`.
- Red-green observed on all three code changes: Background tests failed with the scenario's Given empty; `feature_doc_from_json` tests failed with `ImportError`; shim tests could not import `training/serve/app.py` before it existed.
- Corpus scan progression, all measured: **45 kept** (3 repos, no Background support) → **107 kept** (same 3 repos, Background support added) → **213 kept** (6 repos).
- Full build: 213 documents → 182 pairs (31 dropped in back-generation), 164 train / 18 holdout, 2m31s, 213 gpt-4o calls.
- Live DB re-scan with the JSON branch: `--db-source all` went from `unparsable 8 · kept 0` to `unparsable 0 · kept 8`.
- Dataset verified by re-reading both files independently of the builder: 182 records, **0 validation problems** across five checks — JSON parses, roles are system/user/assistant, every system message matches `BDD_SYSTEM_PROMPT` byte-for-byte, every assistant message validates as `BDDGenerateResponse`, no empty `scenarios` arrays. Train/holdout origin sets disjoint.
- `git status` confirms `training/data/` (398 KB train + 49 KB holdout) and `training/corpus/` (44 MB) are on disk and **absent from git**.

### Completion Notes List

- **AC1 — corpus acquired, gate passed.** Six MIT-licensed repositories, 708 `.feature` files, **213 documents kept**. Sources, licences, commit SHAs and the full scan output are in [CORPUS.md](training/CORPUS.md). `diaspora/diaspora` was cloned, found to be **AGPL-3.0** and removed rather than making that licensing call silently.
- **AC2 — dataset built and independently verified.** 164 train / 18 holdout, checked by re-reading the written files rather than trusting the builder's log line, as the task required.
- **AC3 — JSON branch.** `feature_doc_from_json` maps a serialised `BDDGenerateResponse` straight onto `FeatureDoc` with no Gherkin round-trip. `--db-source uploaded` remains the default, and the help text now says why (self-distillation).
- **AC4 — config and notebook.** Every hyperparameter lives in `config/train_config.yaml`; the notebook reads it and hard-codes nothing. Its final cell prints the `RUN_LOG.md` table ready to paste, so filling the log is transcription. *(Code review: this was not true when written — four values were hard-coded and four keys read by nothing. It is true now, and `test_finetune_notebook.py` keeps it true.)*
- **AC5 — nothing leaked.** No training dependency touched `backend/pyproject.toml`; `training/data/` and `training/corpus/` are git-ignored and verified absent from `git status`.
- **AC6 — serving shim: MET, as narrowed 2026-08-09.** The contract half is done — read off `FineTunedModelProvider`, `/api/chat` with a `messages` array rather than a hand-rolled prompt, validated against `BDDGenerateResponse` before responding, bounded by `asyncio.timeout` inside the 12s budget. 25 tests. **The template half is verified, not guaranteed**: the mechanism that guaranteed it (Unsloth's `Modelfile`) left with unsloth, so the shim now compares the served template against the run's `chat_template.jinja` and reports the verdict rather than assuming it. **The shim has never served the real model** — that is Story 6.2's, along with the PEFT → servable conversion it requires.
- **AC7 — met.** `RUN_LOG.md` carries every required field with measured values from the completed Kaggle run, plus the artefact paths Stories 6.2/6.3 need.
- **AC8 — honoured.** The run was executed, by a human, from the Kaggle UI; nothing about it is inferred. The one thing that was claimed without being done — the shim round-trip — was caught by code review and un-ticked rather than left standing.

### 🔑 The discovery that made this story viable: Gherkin `Background:` was unsupported

The first scan of three repositories kept **45 documents / 80 scenarios** — below the story's own ~50 threshold, which required stopping and reporting rather than proceeding quietly.

Investigation found **181 of 474 files (38%) used `Background:` blocks**, which the shared parser did not understand. A Background declares steps once for every scenario in the feature, so scenarios relying on it appeared to have no `Given`, and the entire document was discarded as `incomplete_steps`.

Teaching `parse_feature` to inherit Background steps took the same three repositories from **45 → 107 documents (+138%)** and **80 → 339 scenarios (+324%)**.

This was a **correctness fix, not a relaxed filter** — those scenarios always had a Given. It also fixes a live user-facing bug from Story 6.7: a perfectly valid `.feature` file using a Background was being rejected at upload with *"every scenario needs a complete Given, When and Then"*.

### ⚠️ The corpus is off-domain — read before interpreting any eval loss

**152 of the 182 pairs (84%) come from the test suites of testing frameworks** — behat, aruba, cucumber-ruby/jvm/js. Their Gherkin is about running CLI commands:

> `Given I use the fixture "cli-app"` · `When I run \`rspec\`` · `Then the specs should all pass`

Only 30 pairs (16%, from alphagov/whitehall) describe product behaviour of the kind this application generates from Jira tickets.

A model trained on this will learn the *shape* of Gherkin and how to emit valid `BDDGenerateResponse` JSON, which is genuinely useful. But it has seen little of its actual domain, so **a low eval loss will not mean it beats the general LLM on real tickets**. That is exactly Story 6.3's question, and it should be answered before any serving spend is committed.

The input side is also synthetic — acceptance criteria were reconstructed backwards by an LLM, because the application has never captured a real pair. The output side is genuine human-authored Gherkin.

### ⚠️ Story 6.4's capture has never fired

`SELECT count(*) FROM bdd_files WHERE acceptance_criteria IS NOT NULL` returns **0**. All 10 rows predate the migration. The mechanism is correct and tested; it simply has not been exercised since it shipped. Until the app is used for a real generation, every future dataset stays dependent on back-generated ACs — which is the single biggest quality ceiling on this fine-tune.

### Task 7 attempt 1 — pushed, ran, failed on a Kaggle account prerequisite

Credentials were supplied and **authentication succeeded**. The dataset uploaded, the kernel pushed and ran on Kaggle's GPU, and it **failed** — not in our code:

```
WARNING: Retrying … Failed to establish a new connection:
         [Errno -3] Temporary failure in name resolution   (×10)
ERROR: Could not find a version that satisfies the requirement unsloth
ERROR: Could not find a version that satisfies the requirement trl<0.20.0
```

**The kernel had no internet.** Kaggle *stored* `enable_internet: true` — verified by pulling the kernel metadata back — but only honours it for **phone-verified accounts**. An unverified account runs with no DNS and no warning, so the metadata looks correct and the log blames pip.

There is no workaround inside the kernel: without network the base model cannot be downloaded either. **Fix is one action on the account: kaggle.com → Settings → Phone Verification, then re-push.**

Three real bugs in the runner were found and fixed by this attempt, all of which would have recurred:

- **cp1252 crash** — the script died on its own `→` progress character on a Windows console. Now reconfigures stdout/stderr to UTF-8 and uses ASCII markers.
- **Slug mismatch** — Kaggle derives a kernel's slug from its **title**, not the `id` supplied, so the push silently created `bdd-fine-tune-story-6-5` and every later call 403'd with a *permissions* message. `_resolve_kernel_ref()` now finds the kernel by title.
- **Status parsing** — `kernels_status` returns a `KernelWorkerStatus` IntEnum, so the state rendered as `1`. Now reads `.name`, and handles dict or object responses.

Added `diagnose()`, which turns the three failure signatures that actually happen (no internet, gated model, CUDA OOM) into the cause and its fix. Verified against the real failed log rather than a synthetic one.

### Task 7 attempts 2-4 — internet fixed, now blocked on GPU model

Phone verification worked. Attempt 2 got much further: `pip install unsloth` succeeded, the config loaded, and the base model began downloading. It then failed with:

```
Tesla P100-PCIE-16GB with CUDA capability sm_60 is not compatible
The current PyTorch install supports sm_70 sm_75 sm_80 sm_86 sm_90 sm_100 sm_120
AcceleratorError: CUDA error: no kernel image is available for execution on the device
```

**Kaggle allocated a P100 (sm_60); current PyTorch wheels build for sm_70 and up.** The Kaggle API exposes only `enable_gpu` / `enable_tpu` — verified by introspecting `ApiSaveKernelRequest` — so the GPU *type* cannot be requested programmatically. Pushing a fresh kernel got a P100 again.

**Confirmed: an API `kernels_push` resets the session accelerator to the account default (P100), discarding a T4 chosen in the UI.** Proven by contrast — the UI-triggered run reported `GPU: Tesla T4 (sm_75)` and passed pre-flight, while the very next API push on the same kernel reported `Tesla P100 (sm_60)` again. Consequence: **the code can be pushed by API, but the run must be started from the UI.**

**Attempts 6-7 (UI, T4) reached actual training.** Pre-flight passed, the model loaded, LoRA attached, training began — then died with `AttributeError: 'int' object has no attribute 'mean'` inside unsloth's monkey-patched training step. My first diagnosis blamed our own `--no-deps "trl<0.20.0"` pin; removing it changed nothing, so that was wrong. It is a genuine version skew between the installed unsloth and the transformers on Kaggle's image.

**Attempt 8 removes unsloth from the path entirely.** The training code now lives in `training/finetune_bdd.py` — plain `transformers` + `BitsAndBytesConfig` for 4-bit, `peft` for the adapter, and `transformers.Trainer` with `DataCollatorForLanguageModeling`. No unsloth, and no `trl.SFTTrainer` either (its constructor signature has moved between releases — the same class of hazard).

The reasoning: unsloth buys speed, and on 164 examples there is no speed worth buying. What it costs is a monkey-patched training loop whose compatibility with the host image we cannot control and cannot test locally. Everything in the replacement is API surface that has been stable for years. The notebook is now **generated from that script**, so the two cannot drift.

Losses accepted: the GGUF/Modelfile export was unsloth-specific and is gone; the adapter is still saved, and GGUF conversion can be done separately if Story 6.2 needs it.

**Why there is no software workaround.** The log shows no torch download, so `2.10.0+cu128` is Kaggle's **stock image** torch — their own image ships a build that excludes sm_60. Fixing it in the notebook would mean downgrading torch (~2.5 GB) and breaking the rest of the preinstalled stack. Rejected.

**Remaining action — start the run from the Kaggle UI, not the API:**

1. Open <https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5>
2. Right panel → **Session options → Accelerator → "GPU T4 x2"**
3. **Save Version → "Save & Run All (Commit)"**

The dataset is already attached and the notebook is current, so nothing else needs uploading. Once it finishes, `python training/kaggle_run.py --fetch` pulls the log and prints the `RUN_LOG.md` table. The division is now exact: the UI does the one thing the API cannot (choose the GPU and start the session); everything else stays automated.

Two more diagnostics came out of these attempts:

- **A GPU pre-flight cell** now runs before model loading: it prints the device, its capability and `torch.cuda.get_arch_list()`, and aborts immediately with the exact fix if they are incompatible. This turned a 7-minute cryptic `AcceleratorError` into a ~40-second explicit one. Verified against a real P100 run.
- **`kernels_output` crashed on Windows**, writing the log with cp1252 and producing a **0-byte file** — which had made attempt 2 look like it "failed before producing output" when in fact the download was at fault. Needs `PYTHONUTF8=1`. The Kaggle log is also a JSON stream, not plain text, so the `RUN_LOG` row matcher never fired on it.
- `diagnose()` now recognises the sm_60 signature alongside no-internet, gated-model and OOM.

### ✅ Task 7 attempt 8 — COMPLETE (2026-08-09)

Removing unsloth worked on the first attempt. Kernel status **COMPLETE**, 20.9 min on a Tesla T4:

| | |
|---|---|
| Base model | `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` (loaded via transformers) |
| Trainable params | 40,370,176 / 7,655,986,688 (**0.527%**) |
| Final train loss | **0.3483** |
| **Final eval loss** | **0.3976** |
| Emits parseable `BDDGenerateResponse` | **yes** |
| Adapter | 161.5 MB, pulled to `training/outputs/` (git-ignored) |

All measured values are in [RUN_LOG.md](training/RUN_LOG.md). The adapter exists, so **Story 6.2 has something to integrate and Story 6.3 has something to evaluate** — the whole point of this story.

**The honest read on quality.** Given an unseen AC pair, the model emitted structurally perfect output: valid JSON, correct schema, one scenario per clause, traceability populated. But AC2 was about *link expiry*, and the model produced a `when` identical to AC1's with the `then` merely negated. It learned the shape convincingly and the reasoning barely at all — exactly the failure mode predicted from a corpus that is 84% testing-framework Gherkin with synthetic acceptance criteria. **The eval loss does not show this**, which is why Story 6.3 must run before any serving spend.

**Eight attempts, seven infrastructure failures**, none in the dataset or the training config: Kaggle key format → missing username → phone verification (no DNS) → P100 vs sm_70+ torch → API push resetting the accelerator → unsloth version skew (twice, the first diagnosis wrong). Each is now either fixed in `kaggle_run.py`, caught by the notebook's pre-flight, or named by `diagnose()`.

### 🔎 Code review 2026-08-09 — 3 High, 5 Medium fixed

An adversarial review found that **removing unsloth in attempt 8 quietly broke
AC6, and nothing was walked back.** Unsloth's `Modelfile` export was the only
thing carrying the training chat template into serving; when it went, the
guarantee went with it while `serve/app.py` and its README kept asserting it.
Three findings were the same wound.

| # | Sev | Finding | Fix |
|---|---|---|---|
| H1 | High | Task 7's last subtask — *"re-run the shim against the real model … inside 12s"* — was `[x]` but never done, and cannot be as built: the run yields a PEFT adapter, which Ollama cannot load. `RUN_LOG`'s "emits parseable `BDDGenerateResponse`" was measured in-process by `finetune_bdd.py:219`, not via the shim | Subtask un-ticked and marked **OPEN** with what unblocks it; `RUN_LOG.md` and `serve/README.md` now state the caveat rather than implying a proven path |
| H2 | High | AC6's chat-template guarantee unenforced; `app.py:19` and `README:26-31` still credited an Unsloth Modelfile that no longer exists | The run writes `chat_template.jinja` beside the adapter; the shim compares it against what Ollama reports (`/api/show`) and publishes `match` / `mismatch` / `unknown` on `/health` and at startup. **`unknown` is deliberately not a pass** |
| H3 | High | AC4's "single source of truth" broken: `use_gradient_checkpointing: "unsloth"`, `export_gguf`, `gguf_quantization`, `chat_template` were read by nothing, while `fp16`, `nf4`, double-quant and `max_new_tokens=768` were hard-coded. The config *promised a GGUF export* the README then told you to use | Dead keys removed, live ones wired, hard-coded values moved into config. `test_finetune_notebook.py` now fails if a config key is not read by the script |
| M1 | Med | `finetune_bdd.py` and `KAGGLE.md` tracked but absent from the File List | Added |
| M2 | Med | File List called the notebook "Unsloth QLoRA notebook (10 cells)"; it is 4 cells of plain transformers | Corrected; `sync_notebook.py` + a test now make "generated from the script" mechanically true |
| M3 | Med | Shim had no total time bound — `httpx` timeouts are per-phase, so connect+read ≈ 20s, past the provider's 12s. The test asserted `10 < 12` on a constant, so it passed regardless | `asyncio.timeout` wraps the request, mirroring `fine_tuned_provider.py`; the test now proves the bound fires |
| M4 | Med | `serve/README.md` step 1 was `ollama create … -f outputs/bdd-lora-gguf/Modelfile` — a path never produced | Replaced with the real merge → GGUF → `ollama create` procedure |
| M5 | Med | Adapter unfindable: `RUN_LOG` said "in the kernel output", story said `training/outputs/`; actual is `training/outputs/outputs/bdd-lora/` | Exact paths in `RUN_LOG.md`, `serve/README.md` and the script's printed table |

Three Low findings were left as-is and are recorded in Change Log: committed
`.pyc` (already deleted, only in history), commit-message quality, and a cosmetic
`Path(origin).stem` fallback in `feature_doc_from_json` that only fires on an
empty feature name.

**Verification:** `uv run --directory backend pytest -q` → **425 → 454 passed**
(+29), zero regressions. Ruff: zero new errors on touched files.

### File List

**Backend — modified:**
- `backend/app/services/training_data_service.py` — `BACKGROUND_RE` + Background inheritance in `parse_feature`; `feature_doc_from_json`
- `backend/tests/test_training_data_service.py` — 9 tests (4 Background, 5 JSON branch)
- `backend/tests/test_build_dataset.py` — 5 tests for `content_format` routing

**Backend — created:**
- `backend/tests/test_serving_shim.py` — 14 tests for the shim contract and output guarantee

**Backend — created (code review, 2026-08-09):**
- `backend/tests/test_finetune_notebook.py` — 18 tests: notebook↔script sync, and every `train_config.yaml` key is read by the script

**Training — created:**
- `training/CORPUS.md` — sources, licences, SHAs, scan output, the AGPL exclusion
- `training/RUN_LOG.md` — AC7 fields, filled with measured values after Task 7
- `training/config/train_config.yaml` — single source of hyperparameters
- `training/finetune_bdd.py` — the training script; plain transformers + peft + `Trainer`, no unsloth (attempt 8, the one that worked)
- `training/finetune_bdd.ipynb` — the Kaggle notebook; **generated from `finetune_bdd.py`** by `sync_notebook.py`, 4 cells
- `training/sync_notebook.py` — regenerates the notebook from the script and `--check`s they agree (added by code review, so "generated from the script" is enforced rather than asserted)
- `training/requirements.txt` — local tooling only
- `training/serve/app.py` — HTTP shim implementing the provider contract, with the AC6 chat-template verification
- `training/serve/README.md` — contract, wiring, the adapter→Ollama conversion, the silent-fallback warning
- `training/kaggle_run.py` — automates Task 7: uploads the dataset, pushes a GPU kernel, polls it, fetches the log table
- `training/KAGGLE.md` — the Kaggle runbook: credentials, the phone-verification and P100 traps, the failure-signature table

**Training — modified:**
- `training/build_dataset.py` — `content_format` in the DB query, JSON routing in `_accept`, `--db-source` help text
- `training/README.md` — workflow, file map, supported Gherkin, `content_format` table

**Modified by code review (2026-08-09):**
- `training/config/train_config.yaml` — removed the two dead keys, added the four hard-coded ones
- `training/finetune_bdd.py` — reads every config key; writes `chat_template.jinja` beside the adapter
- `training/finetune_bdd.ipynb` — regenerated from the script
- `training/serve/app.py` — total-time bound; chat-template verification on `/health` and at startup
- `training/serve/README.md` — the adapter→Ollama conversion procedure, the template verdict table
- `training/RUN_LOG.md` — artefact paths; the in-process caveat on "emits parseable `BDDGenerateResponse`"
- `backend/tests/test_serving_shim.py` — 14 → 25 tests

**Repo — modified:**
- `.gitignore` — `training/data/`, `training/corpus/`

**Not committed (git-ignored, on disk):** `training/data/{train,holdout}.jsonl`, `training/corpus/` (6 repositories, 44 MB)

## Change Log

- 2026-08-22: **Retro-documented: three further training runs happened after this story closed, none of them logged here.** All are recorded in `training/RUN_LOG.md` and none changes this story's ACs — the corpus, builder, config and shim it delivered were reused unchanged, which is the point. **Run 3 (2026-08-15)** is a model-size ablation: `train_config.yaml`'s `base_model` moved from `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` to the **1.5B**, with every other value and the dataset held identical so the difference is attributable to size alone — 4.9× smaller, 3.5× faster, ~15% higher eval loss, and it is now the served model (Run 2's adapter is preserved at `training/outputs/run-2-qwen7b/`). **Run 4 (2026-08-16)** reproduced Run 3 exactly and added in-process holdout scoring on the GPU, which gave the eval loss a noise floor and removed the ~62s/item local serving path from the quality metrics. **Run 5 (2026-08-17)** is the first run on the product-domain dataset and belongs to **Story 6.8**. Also note for anyone re-reading AC7: `RUN_LOG.md` now carries per-run coverage, duplicate rate and reference alignment alongside loss, because Runs 3 and 4 pointed in opposite directions on loss and coverage — loss alone does not answer whether the model is good at the task.
- 2026-08-09: **Correct-course — AC6's serving clause moved to Story 6.2; story `done`.** The code review below left one item open: the shim had never served the real model. Investigating why exposed a contradiction in the plan rather than a gap in the work — [epics.md](_bmad-output/planning-artifacts/epics.md) required 6.5 to prove the model *"is servable… within `FINE_TUNED_MODEL_TIMEOUT_SECONDS`"*, while Epic 6's own preamble assigned the serving decision to Story 6.2, and [architecture.md](_bmad-output/planning-artifacts/architecture.md) listed serving infrastructure as an unresolved deferral requiring that same story. 6.5 was being asked to demonstrate what 6.2 gates. The correct-course analysis also found that the **PEFT → servable conversion was owned by no story at all**, though both 6.2 and 6.3 depend on it — structurally the same defect the 2026-08-08 proposal found with training itself. Both now belong to 6.2, which additionally had its epic AC corrected from 30s to the configured 12s. AC6 is narrowed to what this story can own and prove: the contract, the validation, the time bound, and a training chat template persisted and verifiable. The open Task 7 subtask was **reassigned, not re-ticked**. See [sprint-change-proposal-2026-08-09.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-08-09.md).
- 2026-08-09: **Code review — 3 High, 5 Medium fixed.** The review's central finding was that removing unsloth in attempt 8 broke AC6 without anything being walked back: its `Modelfile` export was the sole carrier of the training chat template into serving, and `serve/app.py` went on claiming it for a file that no longer exists. The shim now *verifies* the template instead of asserting it — comparing what Ollama serves against a `chat_template.jinja` the run writes beside the adapter, and reporting `match`/`mismatch`/`unknown` on `/health`, with `unknown` explicitly not a pass. Also: the shim gained the `asyncio.timeout` total bound it needed to actually respect the 12s budget (httpx's per-phase timeout permitted roughly double, and the test asserted a constant rather than the behaviour); `train_config.yaml` lost two keys that turned nothing — one of which promised a GGUF export the README then instructed you to use — and gained the four values the script had hard-coded; and the notebook is now regenerated from `finetune_bdd.py` by `sync_notebook.py` with a test that fails on drift. **Task 7's last subtask was un-ticked**: the shim has never served the real model and cannot until the adapter is manually converted to GGUF, so the claim was false. 425 → 454 tests. Three Low findings accepted without change: `training/__pycache__/build_dataset.cpython-312.pyc` was committed in `c053d41`/`c24c686` and deleted in `cdb4935` but remains in history; commit messages for this story are all "changes"/"chges", giving no AC traceability; and `feature_doc_from_json`'s `Path(origin).stem` fallback mangles `db:generated:<uuid>` origins, which is latent because it only fires when a scenario carries an empty feature name.
- 2026-08-09: **Task 7 complete — the model exists.** Training ran to completion on a Kaggle T4 in 20.9 min: Qwen2.5-7B, 4-bit QLoRA r=16, train loss 0.3483, **eval loss 0.3976**, and the tuned model emits parseable `BDDGenerateResponse` JSON. The 161 MB adapter is pulled to `training/outputs/`. Getting there took eight attempts, seven of which failed on infrastructure rather than on the model; the decisive fix was removing unsloth and training on plain transformers + peft + `Trainer`, which worked first time. `RUN_LOG.md` carries the measured values, plus the honest caveat that the model learned Gherkin's shape far better than its reasoning — a corpus problem the eval loss cannot show, and Story 6.3's question to answer.
- 2026-08-08: Implemented Tasks 1-6; **Task 7 (the GPU run) is deliberately open** and the story is NOT marked review. Acquired a 708-file corpus from six MIT-licensed repositories (excluding an AGPL one), built and independently verified a 182-pair dataset, added the `content_format` JSON branch that took the live database from 0 usable rows to 8, committed a config-driven Unsloth QLoRA notebook and an HTTP serving shim matching the contract read off `FineTunedModelProvider`, and scaffolded `RUN_LOG.md` with `TBD` markers. The story-defining discovery was that the shared Gherkin parser did not understand `Background:` blocks — 38% of real feature files use them, and their scenarios were being discarded as incomplete; fixing it more than doubled the corpus from the same repositories and also repaired a live upload bug from Story 6.7. Flagged that 84% of the corpus is testing-framework Gherkin rather than product behaviour, so a low eval loss will not by itself mean the fine-tune beats the general LLM. Backend **397 → 425** tests, zero regressions, zero new lint errors.
- 2026-08-08: Story drafted. Rewrote the epic's opening "Given a corpus…" into an explicit acquisition task after measuring the live database: `bdd_files` yields **0 usable documents** on every `--db-source`, `training_datasets` is empty, and — newly discovered — **not one row carries `acceptance_criteria`**, so Story 6.4's capture has never fired on a real generation. Pinned the serving contract by reading `FineTunedModelProvider` rather than the epic text, which understates the timeout as 30s when the configured budget is 12s. Added the `content_format='json'` branch that currently makes 8 captured rows read as "unparsable". Made the human GPU-run gate explicit, with `RUN_LOG.md` shipping `TBD` markers, because the realistic failure here is an agent inventing hyperparameters and an eval loss that Story 6.3 would then treat as measurements.
