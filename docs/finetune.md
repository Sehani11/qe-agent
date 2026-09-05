# Fine-tuning: what the app does, and what you still do by hand

Porting this pipeline to another app? Most of it is already driven from
`/fine-tune` in the UI. This page is the **complement**: the steps that page
cannot do for you, in the order you hit them.

Everything here is operator work — account setup, licence decisions, a model
format conversion, and a process to run. None of it can be moved into the panel,
and each item says why.

---

## What `/fine-tune` already does

So the manual list below is defined by contrast, not repeated:

| Panel | Does |
|---|---|
| **Training data** | Upload `.feature` / `.jsonl` corpora, listed and deletable. Enforces the same quality filters the dataset builder uses, so nothing is accepted that the builder would later discard |
| **Training** | **Train now** builds the pairs, pushes dataset + kernel to Kaggle, polls the GPU kernel, downloads the adapter to `training/outputs/run-<id>/`, streams the log into the run row. **Download model** serves it as a zip |
| **Evaluation runs** / **Quick comparison** | Scores the fine-tune against the general LLM on a holdout, saves the results |
| **Start over** | Deletes every dataset, run, adapter and evaluation row, the Kaggle dataset and kernels, and the model loaded in the serving runtime |

Notably automated, in case an older doc tells you otherwise: the Kaggle run no
longer needs a browser. `kaggle_run.py` sets `machine_shape` on the push, so the
T4 is requested by API — the instructions saying you must pick the accelerator
in the UI and hit *Save & Run All* are stale.

---

## 1. Deployment shape — `training/` must be on the server

**Why manual:** the backend Docker image deliberately does not ship `training/`.
`backend/Dockerfile` ends with `COPY . .`, so anything under `backend/` lands in
the runtime image; training pulls in torch, transformers and peft, which would
add gigabytes to a service that only ever performs inference. The directory sits
at the repo root to stay out of that build context.

So the fine-tune page works **from a checkout, not from the container**. In your
new app:

- Run the backend from a full checkout, or mount the repo into the container.
- If the repo is not at its usual place relative to the backend package, set
  `TRAINING_REPO_ROOT=/absolute/path/to/repo`.

`GET /api/v1/training/readiness` reports this as a blocker before offering the
button, so a misconfiguration shows up as an explanation rather than a failed
run.

## 2. Kaggle account setup

**Why manual:** it is an account on someone else's service, and one requirement
is a phone number.

1. **Phone-verify the account.** kaggle.com → Settings → Phone Verification.
   Not optional: an unverified account accepts `enable_internet: true`, stores
   it, and ignores it. The kernel then dies at `pip install` with `Temporary
   failure in name resolution`, which reads like a Kaggle outage rather than an
   account setting. It also refuses to enable a GPU.
2. **Check GPU quota** — ~30 h/week free; a run of this size uses well under an
   hour.
3. **Create an API token:** Settings → API → *Create New Token*, which downloads
   `kaggle.json`.
4. **Put both halves in the repo-root `.env`:**

   ```bash
   KAGGLE_USERNAME=your-kaggle-username
   KAGGLE_KEY=0123456789abcdef0123456789abcdef   # exactly 32 lowercase hex chars
   ```

   Both are required — Kaggle authenticates on username + key, and a key alone
   cannot identify an account. If your key has a prefix or a different length it
   belongs to some other service; `kaggle_run.py` warns about the shape up front
   rather than letting you find out as a 401 three steps later.
5. **Install the client** without touching the backend's dependency set:

   ```bash
   uv pip install --project backend kaggle
   ```

   `uv pip install` writes into the venv but not into `backend/pyproject.toml`,
   which matters for the same image-size reason as step 1.

Readiness reports missing or malformed credentials too, and they are checked
**before** the dataset build — that build costs one LLM call per document, and
discovering a bad key afterwards is a bill for nothing.

## 3. Rename the Kaggle artifacts for your app

**Why manual:** they are constants, and two apps pushing the same slugs into one
account would overwrite each other's dataset between a push and its GPU start —
training one app's model on the other's corpus.

In [`training/kaggle_run.py`](../training/kaggle_run.py):

```python
DATASET_SLUG = "bdd-training-data-6-5"
KERNEL_TITLE = "BDD fine-tune"
KERNEL_SLUG  = "bdd-fine-tune"
```

⚠️ **The title must slugify to the slug.** Kaggle derives a kernel's slug from
its **title**, not from the id you send, and silently creates a differently-named
kernel when they disagree — after which every later call 403s with a message
about *permissions* that says nothing about naming. Lowercase the title, turn
spaces into dashes, drop punctuation, and check the two agree.

`_push_to_kaggle` in `backend/app/services/training_run_service.py` repeats the
kernel slug (the backend cannot import the script — `training/` is outside the
image) and has to be changed with it.

## 4. Source a corpus — if you are not using uploads

**Why manual:** licensing is a human decision, so harvesting is deliberately not
automated.

If your users supply `.feature` / `.jsonl` files through the Training data
panel, skip this. To build from public repositories instead, clone permissively
licensed Cucumber / SpecFlow / Behat projects into a directory and point the
builder at it:

```bash
# Free: see what survives the quality filters before spending anything
uv run --project backend python training/build_dataset.py \
    --features-dir training/corpus --dry-run
```

Record what you cloned and under which licence — `training/CORPUS.md` is the
pattern, with sources, licences and commit SHAs.

One thing to know about the data, whatever its source: `build_dataset.py` works
**backwards**. The app does not persist the acceptance criteria that drove a
generation, so the builder takes existing Gherkin and asks an LLM to reconstruct
the AC that would have produced it. The side the model learns to *emit* is
genuine human-authored Gherkin; only the input side is synthetic. It is a
bootstrap. Real captured `AC → Gherkin` pairs — especially human *edits* of
generated output — are the one signal no teacher model can invent, and the
highest-value thing you can add in a new app.

## 5. Choose a base model

**Why manual:** it is a hardware and quality trade-off with a licensing trap, in
[`training/config/train_config.yaml`](../training/config/train_config.yaml).

```yaml
model:
  base_model: "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"
```

⚠️ **Pick an ungated model.** A Kaggle kernel runs unattended with no Hugging
Face token, so a gated base — any Llama derivative — fails there with a 401 that
looks like a network problem. Qwen2.5 is the default specifically because it is
not gated. To use a gated one you must accept its licence *and* supply
`HF_TOKEN` to the kernel.

Size drives whether you can serve it at all: a 7B at q4 needs ~5 GB and runs on
CPU on a small GPU (~45 s/request against a 12 s budget); a 1.5B at q4 is ~1.1 GB
and fits in VRAM. Expect quality to drop with size — and note that a smaller
model cannot fix a data problem.

## 6. Convert the adapter to GGUF and register it with Ollama

**The biggest manual step, and the one people are surprised by.** The run
produces a **PEFT adapter**, not something a serving runtime can load:

```
training/outputs/run-<id>/
  adapter_model.safetensors
  adapter_config.json
  chat_template.jinja          <- the template training used
  tokenizer.json / tokenizer_config.json
```

**Why manual:** the GGUF export was Unsloth-specific and left when Unsloth was
removed mid-story after it broke on Kaggle. The conversion needs `llama.cpp` and
an isolated Python environment — its requirements pin `numpy~=1.26.4`, which
conflicts with a modern numpy, so these must **never** be installed into
`backend/`.

```bash
# 1. Pull the SAME base the adapter trained on.
ollama pull qwen2.5:1.5b-instruct

# 2. llama.cpp plus an isolated venv for the converter.
git clone --depth 1 https://github.com/ggml-org/llama.cpp
uv venv convert-venv --python 3.12
uv pip install --python convert-venv/Scripts/python.exe \
   --index-strategy unsafe-best-match \
   --extra-index-url https://download.pytorch.org/whl/cpu \
   "torch==2.11.0" "transformers==4.57.6" "numpy~=1.26.4" \
   "sentencepiece>=0.1.98,<0.3.0" "gguf>=0.1.0" "protobuf>=4.21.0,<5.0.0"

# 3. Convert the adapter alone — no base weights downloaded, ~30 s.
#    lora_path is POSITIONAL; --base-model-id fetches config + tokenizer only.
convert-venv/Scripts/python.exe llama.cpp/convert_lora_to_gguf.py \
   training/outputs/run-<id> \
   --base-model-id "Qwen/Qwen2.5-1.5B-Instruct" \
   --outfile training/outputs/bdd-lora-1.5b-f16.gguf --outtype f16

# 4. Register it. training/outputs/Modelfile-1.5b is two lines:
#      FROM qwen2.5:1.5b-instruct
#      ADAPTER ./bdd-lora-1.5b-f16.gguf
ollama create bdd-lora-1.5b -f training/outputs/Modelfile-1.5b
```

⚠️ **`--index-strategy unsafe-best-match` is required.** Without it uv refuses
`transformers==4.57.6`, because llama.cpp's requirements list a PyTorch index
first and uv will not cross indexes by default. The install silently resolves to
nothing, and the converter then fails with `ModuleNotFoundError: transformers`.

⚠️ **Do NOT add a `TEMPLATE` line to the Modelfile.** `chat_template.jinja` is
**Jinja**; Ollama Modelfiles take **Go** templates. Pasting the Jinja in breaks
the prompt format — the exact failure it was meant to prevent. The stock base
already carries the equivalent template, and the shim verifies it for you.

⚠️ **Ollama's native safetensors `ADAPTER` does not work for Qwen.** Its import
guide supports Llama, Mistral and Gemma adapters only, and warns against QLoRA
adapters, which this is. Two minutes to try, but expect
`Error: no Modelfile or safetensors files found`.

**Known exposure — read before trusting any quality comparison.** The adapter
trains on a bitsandbytes **NF4** base and is served on Ollama's **q4_K_M** — the
same weights under different quantization schemes. The template check cannot
detect this: it compares *prompt formats*, not weights, and will happily report
`match` on a model degraded by quantization drift. If a result has to be
defensible, run the same holdout prompts against the adapter in `transformers`
as trained and compare; a large gap indicts serving rather than the fine-tune.

## 7. Run the serving shim

**Why manual:** it is a separate process, outside the backend, that someone
starts and stops.

```bash
uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000
```

Then check the template verdict, which is logged at startup and served on
`/health`:

```bash
curl -s localhost:9000/health | jq .chat_template
{"verified": true, "status": "match", "detail": "Serving uses the training template."}
```

| `status` | Means |
|---|---|
| `match` | Serving reproduces the training template. The only passing state |
| `mismatch` | Different templates. Quality degrades silently — rebuild the Ollama model |
| `unknown` | One side could not be read (Ollama down, or no adapter present). **Not** a pass |

Its own environment:

| Variable | Default | Purpose |
|---|---|---|
| `FINE_TUNED_OLLAMA_MODEL` | `bdd-lora` | The name you used in `ollama create` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama host |
| `FINE_TUNED_ADAPTER_DIR` | `training/outputs/outputs/bdd-lora` | Where `chat_template.jinja` lives — point this at your run directory |
| `SHIM_TIMEOUT_SECONDS` | `10` | Total budget; must stay under the backend's 12 s |

## 8. Point the backend at it

**Why manual:** deployment configuration.

```bash
BDD_MODEL_PROVIDER=fine_tuned
FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
```

Optional: `FINE_TUNED_MODEL_API_KEY` if you put the shim behind one, and
`FINE_TUNED_ALLOW_FALLBACK=false` when it matters that `fine_tuned` *means*
fine-tuned — a demo or a screenshot must not be silently served by the general
LLM under the fine-tuned label.

The **Use fine-tuned model** toggle in the app is gated on all of this actually
working: the backend asks the shim what it serves, then asks the runtime whether
that model is really loaded. If the toggle is disabled, its subtitle says which
of the two is missing.

---

## Verifying it end to end

Three checks, in the order they fail:

1. `curl -s localhost:9000/health | jq .chat_template` → `"status": "match"`.
2. `GET /api/v1/bdd/fine-tuned-status` → `{"available": true, "model": "..."}`.
   The toggle reads this; if it is `false`, the `detail` says why.
3. Generate something and read the **backend** log:

   ```
   bdd_model.generation session_id=… configured=fine_tuned effective=fine_tuned reason=none
   ```

## Two things that will waste your afternoon

**A failure here looks like success in the UI.** When the shim is down, slow, or
returns something unparseable, the provider catches it, falls back to the general
LLM, and returns a perfectly good answer. The only signal is that attribution
line. If `effective` is not `fine_tuned`, your fine-tune is not being used —
check the log, not the output.

**The budget is 12 seconds, not 30.** `FINE_TUNED_MODEL_TIMEOUT_SECONDS` bounds
the *whole* exchange, because the general-LLM fallback still has to run
afterwards inside the 30 s NFR. Do not raise it to make a slow model "fit": that
converts a fast, invisible degradation into a slow one that breaches the NFR. A
7–8B model on CPU will not make that budget — serve it on a GPU, or accept that
every request silently falls back.

## What the app leaves in other people's systems

Worth knowing when you tear a deployment down. **Start over** now removes all of
it, but if you clear things by hand, these are the copies that outlive the
database rows:

| Where | What |
|---|---|
| Kaggle | The private dataset and every kernel pushed, including `--fresh` timestamped ones |
| `training/outputs/` | Adapters, converted `.gguf` files, Modelfiles, kernel logs |
| `training/.kaggle-staging/` | A copy of the uploaded dataset and the downloaded output |
| Ollama's blob store | `ollama create` **copies** the GGUF into `~/.ollama/models`. Deleting the source file leaves the model serving happily — `ollama rm <name>` is what removes it |

## Reference

| Document | Covers |
|---|---|
| [`training/README.md`](../training/README.md) | The pipeline, and driving it by hand |
| [`training/KAGGLE.md`](../training/KAGGLE.md) | Kaggle setup and every gotcha — read before a first run |
| [`training/serve/README.md`](../training/serve/README.md) | The serving contract, the conversion paths that failed, the serving decision |
| [`training/CORPUS.md`](../training/CORPUS.md) | Corpus sources, licences, commit SHAs |
| [`training/RUN_LOG.md`](../training/RUN_LOG.md) | Measured results of each run |
| [`docs/evaluation-report.md`](evaluation-report.md) | Whether the fine-tune beat the general LLM |
