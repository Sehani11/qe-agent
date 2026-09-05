# Epic 6 Explained — Fine-Tuned Model Training & Integration

*A plain-language guide to what Epic 6 is, how it works, how to run and test it,
and how the fine-tuning actually happens.*

> **Status: DONE (all 7 stories, 2026-08-13).** A model was trained and served
> end-to-end, but the final decision is **local-only for research** — the
> general LLM (`general_llm`) is still what production uses. Why is explained
> at the bottom.

---

## 1. What is Epic 6, in one paragraph?

The app generates BDD test scenarios (Gherkin: Given/When/Then) from Jira
acceptance criteria using a big general-purpose LLM (Claude/OpenAI). Epic 6
asked: **can we train our own small, specialized model to do that one job
instead?** So it built the whole pipeline: collect training data, train a model
on a free GPU (Kaggle), plug it into the backend behind a switch, and measure
whether it's actually better than the general LLM. It is a **Phase 2 /
research** epic — nothing in it changed what production users see.

## 2. The 7 stories (what each one did)

Executed in this order: **6.1 → 6.4 → 6.6 → 6.7 → 6.5 → 6.2 → 6.3**
(not numeric order — data capture had to ship first because pairs can't be
captured retroactively).

| Story | Name | In simple words |
|---|---|---|
| 6.1 | Provider abstraction | A "switch" in the backend: `BDD_MODEL_PROVIDER` env var picks which model generates BDD (`general_llm` or `fine_tuned`) |
| 6.4 | Training data capture | Every generated/uploaded/edited BDD file is saved in the `bdd_files` table so it can become training data later |
| 6.6 | Opt-out control | `TRAINING_DATA_OPT_IN` env var stamps each row at write time; opted-out rows are never used for training |
| 6.7 | Manual dataset upload | A page where you upload your own `.feature` or `.jsonl` files as training data (stored in `training_datasets` + Supabase Storage) |
| 6.5 | Dataset + training | Build the dataset (`training/build_dataset.py`) and train a LoRA fine-tune of Qwen2.5-7B on Kaggle. **This is the fine-tuning story** |
| 6.2 | Integration | Serve the trained model locally through Ollama + a small HTTP "shim", and prove the backend can use it end-to-end |
| 6.3 | Evaluation | Automatically compare fine-tuned vs general LLM on the same inputs and write a report (`docs/evaluation-report.md`) |

## 3. How it works — the runtime picture

```
User clicks "Generate BDD"
        │
        ▼
backend bdd_service ──► factory reads BDD_MODEL_PROVIDER
        │
        ├── "general_llm"  ──► normal LLM call (Claude/OpenAI)   ← production default
        │
        └── "fine_tuned"   ──► POST http://localhost:9000/  (the "shim", training/serve/app.py)
                                       │
                                       ▼
                               Ollama (localhost:11434) running "bdd-lora"
                               = Qwen2.5-7B base + our trained LoRA adapter
```

Key facts:

- **The switch** lives in [factory.py](../backend/app/services/bdd_model/factory.py).
  Two providers exist: `general_llm` (default) and `fine_tuned`.
- **The shim** (`training/serve/app.py`) is a tiny FastAPI app that translates
  the backend's request into an Ollama chat call and validates the response is
  a parseable `BDDGenerateResponse` before returning it.
- **It never breaks the user.** If the fine-tuned model is down, slow (>12s
  timeout), or returns garbage, the backend silently **falls back** to the
  general LLM. The only way to know which model actually answered is this
  backend log line — **check the log, not the output**:

  ```
  bdd_model.generation session_id=… configured=fine_tuned effective=fine_tuned reason=none
  ```

  If `effective=general_llm` when you configured `fine_tuned`, your fine-tune
  is NOT being used.

## 4. How fine-tuning works (the simple version)

### The idea

We do **not** train a model from scratch (that costs millions). We take an
existing open model — **Qwen2.5-7B-Instruct** — and teach it one narrow skill:
*"given acceptance criteria, output BDD scenarios as JSON"*.

### The trick: QLoRA

Training all 7.6 billion parameters won't fit on a free GPU. Two tricks make
it fit on a free Kaggle T4 (16 GB):

1. **Q = Quantization (4-bit):** load the base model compressed to 4 bits per
   weight so it fits in memory.
2. **LoRA:** freeze the whole base model and train only tiny "adapter" layers
   bolted onto it — here just **0.5% of the parameters (~40M)**. The output of
   training is not a new model, it's a small **161 MB adapter file** that gets
   attached to the base model at serving time.

### The training data (the clever/awkward part)

A fine-tune needs pairs: *input (acceptance criteria) → output (Gherkin)*.
Problem: the app never stored real pairs (ACs weren't persisted, edits weren't
saved). So `training/build_dataset.py` works **backwards**:

1. Clone public repos full of human-written `.feature` files (the "corpus").
2. Parse and quality-filter them (must have Given/When/Then, no TODOs, etc.).
3. Ask an LLM to **reconstruct** the acceptance criteria that *would have*
   produced each file ("back-generation"). So the output side is real human
   Gherkin; the input side is synthetic.
4. Write `train.jsonl` (164 pairs) and `holdout.jsonl` (18 pairs), split **by
   origin file** so near-duplicate scenarios can't leak across the split.

This is a bootstrap. The real fix — capturing genuine pairs from app usage —
is what stories 6.4/6.6/6.7 exist for.

### The training run itself

`training/finetune_bdd.py` — plain HuggingFace `transformers` + `peft` +
`Trainer` (unsloth was removed after it kept crashing on Kaggle). All
hyperparameters live in one file: `training/config/train_config.yaml`.

Run 1 (2026-08-09, Kaggle T4): **3 epochs, ~21 minutes, final eval loss
0.3976**. Full details in [training/RUN_LOG.md](../training/RUN_LOG.md).

## 5. How to run it — step by step

> Full detail: [training/README.md](../training/README.md),
> [training/KAGGLE.md](../training/KAGGLE.md),
> [training/serve/README.md](../training/serve/README.md). This is the short map.

### Step A — Build the dataset

```bash
# clone permissively-licensed Gherkin repos into training/corpus/ first, then:

# dry run: see what survives the quality filters (free, no LLM calls)
uv run --project backend python training/build_dataset.py --features-dir training/corpus --dry-run

# real build: one LLM call per document (uses backend/.env LLM credentials)
uv run --project backend python training/build_dataset.py --features-dir training/corpus --out training/data
```

Other data sources: `--from-db` (captured `bdd_files` rows, Story 6.4) and
`--from-uploads` (Story 6.7 uploads). Opted-out rows are always excluded.

### Step B — Train on Kaggle (~21 min on a free T4)

Needs a **phone-verified** Kaggle account and `KAGGLE_USERNAME`/`KAGGLE_KEY`
in the repo-root `.env`.

```bash
# 1. push dataset + notebook to Kaggle (API)
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --push

# 2. IN THE BROWSER: open the printed kernel URL,
#    set Accelerator = "GPU T4 x2", then "Save & Run All (Commit)"
#    (an API-triggered run silently resets you to a P100, which crashes PyTorch)

# 3. fetch the trained adapter + results table
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --fetch
```

The adapter lands in `training/outputs/outputs/bdd-lora/`. Read
[KAGGLE.md](../training/KAGGLE.md)'s "five things that will bite you" before
your first run — 7 of the first 8 attempts failed on Kaggle quirks, not the model.

### Step C — Serve it locally

```bash
# 1. one-time: pull the base and register the adapter in Ollama
ollama pull qwen2.5:7b-instruct
#    (convert adapter to GGUF first — exact commands in training/serve/README.md)
ollama create bdd-lora -f training/outputs/Modelfile

# 2. start the shim
uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000

# 3. sanity check — must say "match"
curl -s localhost:9000/health | jq .chat_template

# 4. point the backend at it (env vars)
BDD_MODEL_PROVIDER=fine_tuned
FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
```

Then generate BDD in the app as normal, and confirm the log line says
`effective=fine_tuned`.

## 6. How to test it

**Unit/integration tests** run with the normal backend test command:

```bash
uv run --project backend pytest backend/tests/test_serving_shim.py   # shim contract
uv run --project backend pytest                                       # everything
```

**The evaluation pipeline (Story 6.3)** is the real "is it any good?" test —
it runs the same inputs through both providers and scores them:

```bash
# with the shim running and, FOR THIS RUN ONLY:
#   FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
#   FINE_TUNED_MODEL_TIMEOUT_SECONDS=300     (12s would make every call fall back
#                                             and you'd compare the LLM with itself)

uv run --project backend python -m app.evaluate_models --run-id run-1
uv run --project backend python -m app.evaluate_models --run-id run-1 --resume  # continue an interrupted run
```

It writes [docs/evaluation-report.md](evaluation-report.md) and has built-in
guards (`--check-config`, a "trust gate") against accidentally producing a fake
comparison.

## 7. What was the result? (and why it's not in production)

The evaluation (28 items, both providers) was a **split**:

| Metric | General LLM | Fine-tune |
|---|---|---|
| AC-clause coverage (on-domain) | **1.000** | 0.867 |
| Duplicate-scenario rate | **0.000** | 0.125 |
| Latency | **~3.5 s** | ~45–72 s (CPU) |
| Reference alignment, off-domain | 0.176 | **0.488** |

**Interpretation:** the fine-tuning *worked* — the model genuinely learned the
corpus it was trained on (it wins the only metric grounded in human-authored
Gherkin). But it **learned the wrong corpus**: 84% of training pairs came from
testing-framework repos (Gherkin about CLI commands), not product-domain
Gherkin, and every input side was synthetic. It learned the *shape* of the task
but not the *reasoning*. It's also far too slow without a GPU (45s vs the 12s
production budget).

**Decision:** local-only for research; `general_llm` stays the default. The
remedy is **better data, not better hardware** — real captured AC→Gherkin
pairs (Story 6.4's capture + 6.7's product-domain uploads), then retrain.
Switching the fine-tune on later is just the two env vars in Step C.

## 8. Gotchas cheat-sheet

| Trap | Rule |
|---|---|
| Kaggle gives you a P100 that crashes PyTorch | Push code via API, but **start runs from the Kaggle UI** with "GPU T4 x2" selected |
| Kaggle "no internet" errors at `pip install` | Phone-verify the Kaggle account — the setting is silently ignored otherwise |
| 0-byte Kaggle log on Windows | Always prefix `PYTHONUTF8=1` |
| Fine-tune looks like it's working but isn't | Fallback is silent — check the log for `effective=fine_tuned` |
| Every eval call falls back | Raise `FINE_TUNED_MODEL_TIMEOUT_SECONDS` to 300 **for the eval run only**; never in committed config (it protects the 30s NFR) |
| `/health` says `unknown` instead of `match` | That is a **failure**, not a pass — Ollama is down or the adapter dir is missing |
| Tempted to add training deps to `backend/pyproject.toml` | Never — the Docker image `COPY . .` would ship gigabytes of CUDA wheels. Everything training-related stays in `training/` |
| Quality scores look off | Training used bnb-NF4 quantization, serving uses Ollama q4_K_M — a known drift; see the "quantization exposure" note in [serve/README.md](../training/serve/README.md) |

## 9. Where everything lives

| Thing | Path |
|---|---|
| Provider switch (6.1) | `backend/app/services/bdd_model/` |
| Data capture (6.4) | `backend/app/models/bdd_file.py`, `backend/app/services/training_data_service.py` |
| Upload API (6.7) | `backend/app/api/v1/training.py` |
| Dataset builder (6.5) | `training/build_dataset.py` |
| Training script + config (6.5) | `training/finetune_bdd.py`, `training/config/train_config.yaml` |
| Kaggle runner + guide (6.5) | `training/kaggle_run.py`, `training/KAGGLE.md` |
| Run results (6.5) | `training/RUN_LOG.md` |
| Serving shim (6.2) | `training/serve/app.py`, `training/serve/README.md` |
| Evaluation CLI (6.3) | `backend/app/evaluate_models.py`, report in `docs/evaluation-report.md` |
| Story files | `_bmad-output/implementation-artifacts/6-*.md` |
