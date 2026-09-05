# Running the fine-tune on Kaggle

Setup and operating guide for `training/kaggle_run.py`. Story 6.5's first
training run took **eight attempts**, seven of which failed on Kaggle
infrastructure rather than on the model. Everything that went wrong is
documented here so nobody pays that cost twice.

**Read [The six things that will bite you](#the-six-things-that-will-bite-you)
before your first run.** Five of them fail *silently* or with an error message
that points somewhere other than the cause.

---

## 1. Prerequisites

| Requirement | Why | How to check |
|---|---|---|
| Kaggle account | Obviously | — |
| **Phone verification** | Without it, notebooks run with **no internet** and no warning — `enable_internet: true` is accepted, stored, and ignored | kaggle.com → Settings → Phone Verification |
| GPU quota | ~30 h/week free; this run uses ~21 min | kaggle.com → Settings |

Phone verification is not optional. An unverified account produces a kernel that
fails at `pip install` with `Temporary failure in name resolution`, which reads
like a Kaggle outage rather than an account setting.

## 2. Credentials

Kaggle authenticates on **username + key**. A key alone cannot identify an
account, so both are required.

1. kaggle.com → **Settings → API → "Create New Token"** — downloads a
   `kaggle.json` containing both values.
2. Put them in the repo-root `.env`:

```bash
KAGGLE_USERNAME=your-kaggle-username
KAGGLE_KEY=0123456789abcdef0123456789abcdef      # 32 lowercase hex characters
```

`kaggle_run.py` reads `KAGGLE_*` keys straight out of `.env`; no export needed.
`KAGGLE_API_KEY` is accepted as an alias for `KAGGLE_KEY`.

> **Sanity check the key shape.** A real Kaggle key is exactly **32 lowercase
> hex characters with no prefix**. If yours has a `xx_` prefix or is a different
> length, it belongs to some other service — `kaggle_run.py` warns about this up
> front rather than letting you discover it as a 401 three steps later.

## 3. Install the client

```bash
uv pip install --project backend kaggle
```

`uv pip install` writes into the venv **without** touching
`backend/pyproject.toml`, which matters: the backend Dockerfile ends with
`COPY . .`, so anything added to the backend's dependency set ships in the
production image. `kaggle` is also listed in `training/requirements.txt`, which
is the documentation of record.

## 4. Build the dataset first

The runner refuses to push without one:

```bash
# See what survives the quality filters before spending anything
uv run --project backend python training/build_dataset.py \
    --features-dir training/corpus --dry-run

# Build for real — one LLM call per document
uv run --project backend python training/build_dataset.py \
    --features-dir training/corpus --out training/data
```

See [CORPUS.md](CORPUS.md) for where the corpus comes from and its licences.

## 5. Run it

> **The push now names the accelerator, so the API can start the run.**
> This used to say "push code by API, start runs from the UI", because a push
> reset the session accelerator to the account default (a P100) and there was
> no way to ask for anything else. The Kaggle client has since gained a
> `machine_shape` field, `kernels_push` reads it from `kernel-metadata.json`,
> and `kaggle_run.py` sets it (`MACHINE_SHAPE = "NvidiaTeslaT4"`). Gotchas 2
> and 3 below are kept as history — they explain what the symptom looks like
> if the shape is ever dropped or refused.

**Step 1 — push the code and data (API):**

```bash
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --push
```

This uploads `train.jsonl` / `holdout.jsonl` / `train_config.yaml` as a
**private** dataset and pushes the notebook as a GPU kernel, requesting a T4.
The push starts the run.

**Step 2 — start the run (browser) — only if you need to override anything:**

1. Open the kernel URL the push printed
2. Right panel → **Session options → Accelerator → "GPU T4 x2"**
3. **Save Version → "Save & Run All (Commit)"**

**Step 3 — collect the results (API):**

```bash
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --fetch
```

Prints the `RUN_LOG.md` table ready to paste, or names the failure if it broke.

### Command reference

| Command | Does |
|---|---|
| `--push` | Upload dataset + push kernel |
| `--status` | Poll the kernel once |
| `--fetch` | Download output, print the results table or diagnose the failure |
| `--all` | Push, wait for completion, fetch |
| `--fresh` | Push under a **new** kernel id instead of updating the existing one |

`PYTHONUTF8=1` is not decoration — see gotcha 5.

---

## The six things that will bite you

### 1. No internet, and nothing says so

**Symptom:** every `pip install` fails with `Temporary failure in name
resolution`. The kernel metadata still reports `enable_internet: true`.

**Cause:** Kaggle honours `enable_internet` only for **phone-verified**
accounts. It stores the flag either way, so the configuration looks correct.

**Fix:** verify the phone number on the account. There is no workaround inside
the kernel — without network the base model cannot be downloaded either.

### 2. A P100 that modern PyTorch cannot use

**Symptom:**

```
Tesla P100-PCIE-16GB with CUDA capability sm_60 is not compatible
The current PyTorch install supports sm_70 sm_75 sm_80 sm_86 sm_90 ...
AcceleratorError: CUDA error: no kernel image is available for execution on the device
```

**Cause:** Kaggle allocates either a T4 (sm_75) or a P100 (sm_60). Kaggle's own
stock image ships torch 2.10+cu128, which is **not built for sm_60**.

**Fix:** none needed any more — `kaggle_run.py` names the accelerator on the
push (`MACHINE_SHAPE = "NvidiaTeslaT4"`). This paragraph used to say the API
had no field for GPU *type*; that was true of an older client, and
`ApiSaveKernelRequest` now documents `machine_shape` with the values
`NvidiaTeslaT4`, `NvidiaTeslaP100`, `Tpu1VmV38`.

If the symptom appears anyway, the requested shape was dropped or refused:
check GPU quota and phone verification, then set Accelerator to
**"GPU T4 x2"** in the UI as a fallback.

Downgrading torch in the notebook is not a fix: it means a ~2.5 GB download and
breaks the rest of the preinstalled stack.

The notebook's **pre-flight cell** catches this in ~40 seconds and prints the
exact remedy, instead of failing seven minutes later inside model loading.

### 3. An API push silently resets the accelerator *(historical)*

**Symptom:** you select a T4 in the UI, push an updated notebook, and the run
comes back on a P100.

**Cause:** a push rewrites the session's shape from the pushed metadata. When
that metadata carried no shape, the account default won.

**Fix:** the push now carries one (gotcha 2), so this no longer happens. Kept
because it explains the symptom if `machine_shape` is ever removed from
`kernel-metadata.json`.

### 4. The kernel slug comes from the title, not your id

**Symptom:** the push warns *"Your kernel title does not resolve to the
specified id"*, and every later API call fails with a **permissions** error
that says nothing about naming.

**Cause:** Kaggle derives the slug from the **title**, lowercased with spaces
turned to dashes and punctuation dropped. Supplying `id: bdd-finetune-6-5` with
title `BDD fine-tune (Story 6.5)` creates `bdd-fine-tune-story-6-5` — which is
how the kernel got that name originally.

**Fix:** already handled — `_resolve_kernel_ref()` looks the kernel up by title.
Keep `KERNEL_TITLE` and `KERNEL_SLUG` consistent if you rename anything: they
are now `BDD fine-tune` and `bdd-fine-tune`, and the title slugifies to the
slug exactly. `_push_to_kaggle` in `training_run_service.py` repeats the slug
and has to move with them.

### 5. `kernels_output` corrupts the log on Windows

**Symptom:** a **0-byte** `.log` file, making a failed run look like it produced
no output at all.

**Cause:** the Kaggle client writes the log with the locale encoding (cp1252 on
Windows) and dies on the first non-ASCII character — mid-write, leaving an empty
file.

**Fix:** always run with `PYTHONUTF8=1`.

**Related:** the log is a **JSON stream** (`{"stream_name":…,"data":…}` records
with ANSI escapes), not plain text. Grepping it directly finds nothing;
`kaggle_run.py` parses and de-ANSIs it.

### 6. A fresh notebook running against a stale config

**Symptom:** `KeyError: 'bnb_4bit_quant_type'` (or any other config key) a minute
into the run, from a `train_config.yaml` that demonstrably has the key.

**Cause:** the notebook and the config reach Kaggle through **two different
channels that version independently** — the notebook is pushed as a *kernel*,
while `train_config.yaml` ships inside the *dataset*.

**The root cause was a bug in `kaggle_run.py`, fixed 2026-08-15.** The Kaggle
client does **not raise** when a dataset already exists — it builds an
`ApiCreateDatasetResponse` with `status = "error"` and **returns** it:

```python
if found:
    resp = ApiCreateDatasetResponse()
    resp.status = "error"
    resp.error = f'The requested title "{title}" is already in use...'
    return resp          # <-- returned, not raised
```

`push()` only called `dataset_create_version` from an `except` branch, which
therefore never fired. Every `--push` uploaded a fresh notebook, printed
`created`, and left the dataset **frozen at version 1** — so the kernel kept
reading the pre-unsloth-removal config. `_push_dataset()` now checks the
returned verdict as well as the raised one, and
`backend/tests/test_finetune_notebook.py` pins all four cases.

A stale *editor session* can cause the same symptom independently: an open tab
stays pinned to the dataset version it was opened with. Reopen the kernel URL
after a push.

Made worse by `find()`, which returns the **first** `rglob` hit: an old dataset
version left attached beside a new one resolves silently to the wrong file.

**Fix, in the notebook editor:**

1. Right panel → **Input** → remove `bdd-training-data-6-5`
2. **Add Input → Datasets → Your Datasets** → re-add it (picks up the latest)
3. Accelerator is requested by the push; override in the UI only if needed
4. **Save Version → Save & Run All**

Or push under a brand-new kernel, which always attaches the latest:

```bash
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --push --fresh
```

**Now caught early.** `check_config()` validates every key `main()` reads before
the GPU pre-flight, lists all missing keys at once, and prints the resolved
config path so you can see *which* file won. `finetune_bdd.py` also prints
`config: <path>` on every run — check it matches the dataset version you expect.

### Bonus: unsloth version skew

**Symptom:** training starts, then dies with
`AttributeError: 'int' object has no attribute 'mean'` inside unsloth's patched
training step.

**Cause:** a mismatch between the installed unsloth and the transformers on
Kaggle's image. Pinning `trl` to work around it makes it worse.

**Fix:** Story 6.5 removed unsloth entirely. `training/finetune_bdd.py` uses
plain `transformers` + `BitsAndBytesConfig` + `peft` + `transformers.Trainer` —
no monkey-patching, no `trl.SFTTrainer`. It trained end to end on the first
attempt. On a 164-example dataset there is no speed worth the fragility.

---

## Troubleshooting quick reference

`kaggle_run.py --fetch` recognises each of these and prints the cause and fix.

| Log signature | Cause | Fix |
|---|---|---|
| `Temporary failure in name resolution` | Account not phone-verified | Verify the phone number |
| `no kernel image is available` / `not compatible with the current PyTorch` | P100 (sm_60) allocated — the push should prevent this | Re-push; if it persists, check quota, then Accelerator → GPU T4 x2 |
| `'int' object has no attribute 'mean'` | unsloth ↔ transformers skew | Don't use unsloth; don't pin trl |
| `401 Client Error` / `gated repo` | Base model gated on HF | Use an ungated base, or add `HF_TOKEN` |
| `CUDA out of memory` | Batch/sequence too large | Lower `per_device_train_batch_size` or `max_seq_length` |
| `KeyError` on a config key that exists | Dataset never versioned (client returns, not raises) | Fixed in `_push_dataset()`; re-run `--push` |
| 0-byte `.log` | cp1252 write crash | Re-run with `PYTHONUTF8=1` |
| `Permission 'kernels.get' was denied` | Slug/title mismatch | Handled by `_resolve_kernel_ref()` |

## What a successful run produces

Kernel status **COMPLETE**, roughly 21 minutes on a T4, and these artifacts:

```
adapter_model.safetensors    161 MB   the LoRA adapter
adapter_config.json                   peft config
tokenizer.json / _config.json         tokenizer
chat_template.jinja                   the template training used - the serving
                                      shim must apply the SAME one
```

`--fetch` writes them to `training/outputs/` (git-ignored) and prints the
results table for [RUN_LOG.md](RUN_LOG.md).

### The table has quality metrics, not just loss

After saving the adapter, the run generates against **every holdout item** and
scores it with the same functions the evaluation report uses — AC-clause
coverage, duplicate rate, reference alignment — plus the JSON parse rate. Those
rows come back through `--fetch` with everything else.

This exists because **loss does not answer "is the model any good at the task"**.
Run 3 has a *worse* eval loss than Run 2 and *better* coverage; the two can point
in opposite directions. Scoring on the T4 that just trained the adapter costs a
few minutes, against ~62 s **per item** through the local serving path where the
model does not fit in VRAM.

Each row also prints what the **human-authored references** score on the same
metric, because the targets are not 1.0 and 0.0:

| | Human reference (19-item holdout) |
|---|---|
| AC-clause coverage | 0.974 |
| Duplicate rate | 0.276 |
| Scenarios per item | 2.79 |

A model at 0.30 duplicates is reproducing its training data, not misbehaving.

**Scope.** These numbers are in-process greedy decode, so they are comparable
**between runs of this script** — which is what an ablation needs — and are
**not** interchangeable with [docs/evaluation-report.md](../docs/evaluation-report.md),
which scores a different item set through `training/serve/app.py` and Ollama and
therefore measures the serving path too.

Turn it off with `evaluation.score_holdout: false` if GPU quota is tight; the
run then prints `| Holdout scoring | not run |` rather than quietly omitting the
rows. `evaluation.holdout_max_items` caps the item count for a cheap smoke test —
a partial score is not comparable with a full one, so put it back to `0` for any
run that goes in RUN_LOG.md.

## If Kaggle keeps fighting you

`training/finetune_bdd.py` runs unmodified on **Google Colab**, where you pick
the GPU at session start and no push API undoes it. Upload
`train_config.yaml` + `data/*.jsonl`, paste the script into a cell, call
`main()`. Gotchas 1–5 all disappear; only the GPU-capability pre-flight still
matters, and it is in the script.
