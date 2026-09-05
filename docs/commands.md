# Commands

Everything needed to run the app with the fine-tuned model, evaluate it, and retrain it.

**Shell: Git Bash.** PowerShell syntax (`$env:VAR`, `foreach`) will not work here.
**Current model:** `bdd-lora-1.5b` — Qwen2.5 1.5B + the Run 3 LoRA adapter.

---

## 1. Start everything

Three terminals. Ollama runs as a background service already.

**Terminal 1 — the serving shim**

```bash
cd /e/Projects/Python/qe-agent-v2
export FINE_TUNED_OLLAMA_MODEL=bdd-lora-1.5b
export FINE_TUNED_ADAPTER_DIR="E:/Projects/Python/qe-agent-v2/training/outputs/run-3-qwen1.5b"
export SHIM_TIMEOUT_SECONDS=30
uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000
```

**Terminal 2 — backend**

```bash
cd /e/Projects/Python/qe-agent-v2/backend
uv run uvicorn app.main:app --reload --port 8000
```

**Terminal 3 — frontend**

```bash
cd /e/Projects/Python/qe-agent-v2/frontend
npm run dev
```

App: http://localhost:3000 · API: http://localhost:8000

---

## 2. Verify the fine-tuned model is actually being used

```bash
curl -s localhost:9000/health
```

Two things must be true:

| Field | Required value |
|---|---|
| `model` | `bdd-lora-1.5b` |
| `chat_template.status` | `match` |

`unknown` is a **failure**, not a pass — it means Ollama is unreachable or the adapter
directory is missing.

Then generate BDD in the app and check the backend log:

```
bdd_model.generation … configured=fine_tuned effective=fine_tuned reason=none
```

**`effective=general_llm` means the fine-tune did NOT answer.** The app silently falls back,
so the output looks fine either way. The log is the only reliable evidence.

---

## 3. Stop everything

```bash
for p in 9000 9001 8000 3000; do
  pid=$(netstat -ano | grep LISTENING | grep -E ":$p[[:space:]]" | awk '{print $NF}' | head -1)
  [ -n "$pid" ] && taskkill //PID "$pid" //F
done
```

The double slashes in `//PID //F` are required — Git Bash rewrites single-slash flags as
file paths.

---

## 4. Evaluation

Generation and scoring are **two separate commands**. `--score` only reads rows that already
exist; it never generates.

**Re-build the report from an existing run (seconds, no model calls):**

```bash
cd /e/Projects/Python/qe-agent-v2/backend
uv run python -m app.evaluate_models --run-id run-3-1.5b --score
```

Writes `docs/evaluation-report.md`.

**A fresh run (about 30 minutes):**

```bash
cd /e/Projects/Python/qe-agent-v2/backend
export PYTHONUTF8=1
export FINE_TUNED_MODEL_TIMEOUT_SECONDS=300
uv run python -m app.evaluate_models --run-id run-4              # generate
uv run python -m app.evaluate_models --run-id run-4 --score      # then score
```

The shim must be running first, or every call falls back and the run compares the general
model with itself. The pipeline detects this and refuses to produce a summary.

**Useful flags**

| Flag | Effect |
|---|---|
| `--resume` | Skip rows already recorded for this run id (on by default) |
| `--limit N` | Only the first N items |
| `--providers general_llm` | One provider only |
| `--check-config` | Report configuration problems and exit |
| `--judge` | Also run blinded subjective scoring (costs LLM calls) |

**Existing runs in the database**

| Run id | Rows |
|---|---|
| `run-3-1.5b` | 58 — the complete 1.5B evaluation |
| `eval-2026-08-13` | 56 — earlier run |
| `smoke-1p5b` | 2 — single-item test |

---

## 5. Retraining

Only needed if the dataset changes. The seed is fixed, so retraining on unchanged data
reproduces the same adapter.

```bash
cd /e/Projects/Python/qe-agent-v2

# BACK UP FIRST — `--fetch` wipes its output directory, and run-3 is the only adapter left
cp -r training/outputs/run-3-qwen1.5b training/outputs/run-3-backup

export PYTHONUTF8=1
uv run --project backend python training/kaggle_run.py --push
```

Then **in the browser**, on the kernel page the push printed:

1. Close and reopen the tab if it was already open
2. Right panel → **Session options → Accelerator → "GPU T4 x2"**
3. **Save Version → Save & Run All**

An API push resets the accelerator to the account default (a P100), which current PyTorch
cannot use. Always re-select the T4 **after** pushing, and start runs from the UI.

```bash
uv run --project backend python training/kaggle_run.py --fetch
```

Prints the results table for `training/RUN_LOG.md`.

**Rebuilding the dataset** (costs one LLM call per document — only if the corpus changes):

```bash
uv run --project backend python training/build_dataset.py --features-dir training/corpus --dry-run
uv run --project backend python training/build_dataset.py --features-dir training/corpus --out training/data
```

---

## 6. Registering a new adapter in Ollama

After fetching a new adapter, convert and register it:

```bash
cd /e/Projects/Python/qe-agent-v2

convert-venv/Scripts/python.exe llama.cpp/convert_lora_to_gguf.py \
   training/outputs/run-3-qwen1.5b \
   --base-model-id "Qwen/Qwen2.5-1.5B-Instruct" \
   --outfile training/outputs/bdd-lora-1.5b-f16.gguf --outtype f16

ollama create bdd-lora-1.5b -f training/outputs/Modelfile-1.5b
ollama list
```

`Modelfile-1.5b` sets `num_ctx 2048` to match the training sequence length. Do **not** add a
`TEMPLATE` line — `chat_template.jinja` is Jinja and Ollama expects Go templates; the base
model already carries the correct one.

If `convert-venv` or `llama.cpp` are missing:

```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp
uv venv convert-venv --python 3.12
uv pip install --python convert-venv/Scripts/python.exe \
   --index-strategy unsafe-best-match \
   --extra-index-url https://download.pytorch.org/whl/cpu \
   "torch==2.11.0" "transformers==4.57.6" "numpy~=1.26.4" \
   "sentencepiece>=0.1.98,<0.3.0" "gguf>=0.1.0" "protobuf>=4.21.0,<5.0.0"
```

`--index-strategy unsafe-best-match` is required, or uv resolves nothing and the converter
fails with `ModuleNotFoundError: transformers`.

---

## 7. Tests

```bash
cd /e/Projects/Python/qe-agent-v2
uv run --project backend pytest backend/tests -q
python training/sync_notebook.py --check
```

---

## Traps

**Run backend commands from `backend/`, never the repo root.** Settings load `.env` relative
to the working directory, and there are two files. The root `.env` has a dead Supabase host
and no fine-tune keys, so running from the root fails with `ENOTFOUND ... not found` and
silently loses the fine-tuned configuration.

**`--score` does not generate.** `No rows for run_id=X. Generate first.` means you passed a
run id that has no rows — run the generation command without `--score` first.

**Timeouts are different for demo and evaluation.** `backend/.env` uses 35s, which suits
interactive use. Evaluation items with six criteria can take 175s, so raise
`FINE_TUNED_MODEL_TIMEOUT_SECONDS` to 300 for that run only. Never commit the raised value —
12s exists in production so the fallback still fits the 30s limit.

**The model does not fit the GPU.** A 1.5B at four bit is about 1.1 GB and the card has 2 GB,
but the context cache pushes it over, so it runs about half on the processor. Expect 9–48s
depending on how many criteria are in the input.

**Ollama models currently installed:** `bdd-lora-1.5b` and `qwen2.5:1.5b-instruct`. Removing
the base model breaks the adapter — the adapter is only a small file layered on top of it.
