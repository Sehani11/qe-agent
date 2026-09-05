# Fine-tuned model serving shim

Implements the HTTP contract `FineTunedModelProvider` sends, so the backend can
use the fine-tune from Story 6.5 without any change on its side.

## The contract (read off the provider, not the epic)

```http
POST /
{
  "acceptance_criteria": "AC1: ...",           # always sent
  "system_prompt": "You are an expert QA...",  # sent when non-empty
  "response_format": { ...JSON schema... }     # sent when provided
}

200 -> {"scenarios": [{source_ac_clause, feature, scenario, given, when, then}, ...]}
```

The backend feeds the body straight into `BDDGenerateResponse.model_validate()`
([bdd_service.py:71](../../backend/app/services/bdd_service.py#L71)), so a
missing or malformed `scenarios` array fails the generation. The shim validates
before responding rather than passing that problem downstream.

## ✅ Status: verified end to end — 2026-08-09 (Story 6.2)

A real request has been served by the fine-tune, through this shim, through
`bdd_service`:

```
bdd_model.generation session_id=941bb53a-… configured=fine_tuned effective=fine_tuned reason=none
ELAPSED 45.0s  scenarios=2
```

Both AC clauses covered, valid `BDDGenerateResponse`, `chat_template.status =
match`. **But 45s against a 12s budget** — see the serving decision below.

## 🚦 Serving decision (Story 6.2, AC8) — LOCAL-ONLY FOR RESEARCH

**Decision: the fine-tuned model is not served in production. `general_llm`
remains the shipped default.**

> ### ✅ Confirmed by Story 6.3 — 2026-08-13
>
> The decision said "revisit after Story 6.3". Story 6.3 has now run, and it
> **confirms the decision rather than overturning it**. On a complete 10-item
> on-domain comparison with zero degraded rows, the general LLM beat the
> fine-tune on every objective metric: AC-clause coverage **1.000 vs 0.867**,
> duplicate-scenario rate **0.000 vs 0.125**, and latency **3.5s vs 72.6s**.
>
> **Nothing here justifies GPU spend.** What would change the answer is better
> training data — real captured AC→Gherkin pairs instead of back-generated ones
> — not better serving hardware. Full numbers and caveats:
> [docs/evaluation-report.md](../../docs/evaluation-report.md).

| Evidence | Value |
|---|---|
| Measured latency, 2 AC clauses, warm | **45.0 s** |
| `FINE_TUNED_MODEL_TIMEOUT_SECONDS` | **12 s** |
| Dev GPU | MX330, 2 GB VRAM — a 7B q4 needs ~5 GB, so it runs on CPU |
| Production host | `t3.small`, no GPU |
| Model quality | Learned Gherkin's *shape*, not its reasoning ([RUN_LOG.md](../RUN_LOG.md)) |

Serving this in production would mean renting a GPU — and **nothing yet shows
the fine-tune beats the general LLM**, which is precisely Story 6.3's question.
Paying for inference before that answer exists is spending on an unmeasured
benefit. [prd.md:187](../../_bmad-output/planning-artifacts/prd.md#L187) already
carries this contingency: *"Fine-tuned model accuracy insufficient → Fallback:
continue using general LLM with enhanced prompting."*

**What this unblocks:** Story 6.3 can evaluate locally against `bdd-lora` with
no infrastructure spend at all — the artifact and the procedure above are all it
needs. Latency is irrelevant to a batch evaluation.

**To revisit:** if 6.3 shows a real quality win, the options are a GPU instance,
a serverless GPU host (Modal / RunPod / Replicate), or a smaller base model that
fits the 12s budget on modest hardware. Switching back on is two env vars
(`BDD_MODEL_PROVIDER=fine_tuned`, `FINE_TUNED_MODEL_ENDPOINT`) — the integration
is done and tested, so this decision is cheap to reverse.

⚠️ **Do not raise `FINE_TUNED_MODEL_TIMEOUT_SECONDS` to make the fine-tune
"fit".** It is 12s so the general-LLM fallback still completes inside NFR-P3's
30s. Raising it converts a fast, invisible degradation into a slow one that
breaches the NFR.

## Getting the adapter into Ollama

Run 1 saved a PEFT adapter, not a GGUF:

```
training/outputs/outputs/bdd-lora/
  adapter_model.safetensors     161 MB
  adapter_config.json
  chat_template.jinja           <- the template training used
  tokenizer.json / tokenizer_config.json
```

Unsloth's GGUF + `Modelfile` export was the original plan; **unsloth was removed
mid-story** when it broke on Kaggle ([RUN_LOG.md](../RUN_LOG.md)) and the export
went with it. Story 6.2 evaluated three replacement paths and **this is the one
that works** — verified end to end on 2026-08-09.

### ✅ The working path: convert the adapter to GGUF

```bash
# 1. Base model in Ollama (4.7 GB). Must be the SAME base the adapter trained on.
ollama pull qwen2.5:7b-instruct

# 2. llama.cpp + an ISOLATED venv for the converter.
#    Its requirements pin numpy~=1.26.4, which conflicts with a modern numpy —
#    never install these into backend/.
git clone --depth 1 https://github.com/ggml-org/llama.cpp
uv venv convert-venv --python 3.12
uv pip install --python convert-venv/Scripts/python.exe \
   --index-strategy unsafe-best-match \
   --extra-index-url https://download.pytorch.org/whl/cpu \
   "torch==2.11.0" "transformers==4.57.6" "numpy~=1.26.4" \
   "sentencepiece>=0.1.98,<0.3.0" "gguf>=0.1.0" "protobuf>=4.21.0,<5.0.0"

# 3. Convert the adapter alone — 80.7 MB out, ~30 s, no base weights downloaded.
#    lora_path is POSITIONAL. --base-model-id fetches config+tokenizer only.
convert-venv/Scripts/python.exe llama.cpp/convert_lora_to_gguf.py \
   training/outputs/outputs/bdd-lora \
   --base-model-id "Qwen/Qwen2.5-7B-Instruct" \
   --outfile training/outputs/bdd-lora-f16.gguf --outtype f16

# 4. Register it. Two lines — see the TEMPLATE note below.
#    training/outputs/Modelfile:
#      FROM qwen2.5:7b-instruct
#      ADAPTER ./bdd-lora-f16.gguf
ollama create bdd-lora -f training/outputs/Modelfile
```

⚠️ **`--index-strategy unsafe-best-match` is required.** Without it uv refuses
`transformers==4.57.6`, because llama.cpp's requirements list a PyTorch index
first and uv will not cross indexes by default. The install silently resolves to
nothing and the converter then fails with `ModuleNotFoundError: transformers`.

### 🚨 Known exposure: the base is quantized differently than training

**Read this before trusting any quality comparison, including Story 6.3's.**

| | |
|---|---|
| Trained on | `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` — bitsandbytes **NF4** |
| Served on | Ollama `qwen2.5:7b-instruct` — **q4_K_M** of the fp16 base |

Same underlying weights, **different quantization schemes**. Ollama's import
guide requires the identical base *"otherwise you will get erratic results"*,
and advises against QLoRA adapters generally — this is one. Path A's failure is
not the only place that warning applies; **Path B carries the same exposure**,
and it is not a theoretical one.

**The template check cannot detect this.** `/health`'s `chat_template` compares
*prompt formats*, not weights. It will report `match` on a model degraded by
quantization drift, because the prompt format genuinely is correct.

Observed so far: output is coherent and covers each AC clause — better than the
in-kernel sample in [RUN_LOG.md](../RUN_LOG.md). That argues the damage is not
severe. **It is not evidence that there is none**, and one prompt is not a
quantization study.

**For Story 6.3:** a poor score here has (at least) two candidate causes — the
fine-tune's own weakness on an off-domain corpus, and quantization drift between
training and serving. Do not attribute a result to the first without ruling out
the second. The cheapest control is to run the same holdout prompts against the
adapter in `transformers` (fp16/NF4, as trained) and compare with the Ollama
path; a large gap indicts serving, a small one indicts the fine-tune. If a
result must be defensible, take Path C — merging into fp16 and converting the
whole model removes the variable entirely, at ~20 GB.

⚠️ **Do NOT add a `TEMPLATE` line to the Modelfile.** `chat_template.jinja` is
**Jinja**; Ollama Modelfiles take **Go** templates. Pasting the Jinja in would
break the prompt format — the exact failure it was meant to prevent. The stock
`qwen2.5:7b-instruct` already carries the equivalent ChatML template, which the
shim verifies for you (see below). Inherit it; do not override it.

### ❌ Path A — Ollama's native safetensors `ADAPTER`: does not work

```dockerfile
FROM qwen2.5:7b-instruct
ADAPTER /path/to/training/outputs/outputs/bdd-lora    # -> Error
```

`Error: no Modelfile or safetensors files found` — reproduced four ways
(absolute path, relative `.`, Windows backslashes, and `ollama create` run from
the weights directory). Ollama's import guide lists supported adapter
architectures as **Llama, Mistral and Gemma**; Qwen2 is not among them, and it
warns against QLoRA adapters, which this is. It is a 2-minute thing to try and
worth trying first — but expect it to fail for Qwen.

### ❌ Path C — merge into fp16 and convert the whole model: not needed

~15 GB base download plus a ~5 GB output. Path B makes it unnecessary. If you
ever do need it, override the base — `adapter_config.json` records the
**bnb-4bit** repo, and merging LoRA weights into already-quantized weights
degrades quality silently. Merge into fp16 `Qwen/Qwen2.5-7B-Instruct`.

## Running it

1. **Start the shim:**

   ```bash
   uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000
   ```

2. **Check the template verdict** — it is logged at startup and served on
   `/health`:

   ```bash
   curl -s localhost:9000/health | jq .chat_template
   {"verified": true, "status": "match", "detail": "Serving uses the training template."}
   ```

3. **Point the backend at it:**

   ```bash
   BDD_MODEL_PROVIDER=fine_tuned
   FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
   ```

### Environment

| Variable | Default | Purpose |
|---|---|---|
| `FINE_TUNED_OLLAMA_MODEL` | `bdd-lora` | Model name registered in Ollama |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama host |
| `SHIM_TIMEOUT_SECONDS` | `10` | Total budget; must stay under the backend's 12s |
| `FINE_TUNED_ADAPTER_DIR` | `training/outputs/outputs/bdd-lora` | Where `chat_template.jinja` lives |

## ⚠️ Two things that will waste your afternoon

**A failure here looks like success in the UI.** When the shim is down, slow, or
returns something unparseable, `FineTunedModelProvider` catches it, falls back to
the general LLM and returns a perfectly good answer. The only signal is the
attribution line in the backend log:

```
bdd_model.generation session_id=… configured=fine_tuned effective=general_llm reason=endpoint_error
```

If `effective` is not `fine_tuned`, your fine-tune is not being used. Check the
log, not the output.

**The budget is 12 seconds, not 30.** `FINE_TUNED_MODEL_TIMEOUT_SECONDS` is 12
and bounds the *whole* exchange via `asyncio.timeout`, because the general-LLM
fallback still has to run afterwards inside NFR-P3's 30s. The shim bounds its
own call the same way — `asyncio.timeout` around the request, not just httpx's
timeout, which is applied per phase (connect/read/write/pool each get the full
value) and would permit roughly double. A 7–8B model on CPU will not make that
budget; serve it somewhere with a GPU, or accept that every request silently
falls back.

## Why `/api/chat` and not `/api/generate`

Sending a `messages` array makes Ollama apply the served model's own chat
template rather than a prompt string built here, which would diverge from
training with no error to notice. `training/build_dataset.py` imports
`BDD_SYSTEM_PROMPT` live from the backend for the same reason.

But "Ollama applies *a* template" is not "Ollama applies *the training*
template" — and after the Unsloth Modelfile export was removed, nothing carries
one to the other automatically. So the shim **verifies** instead of assuming:
on startup and on every `/health`, it compares the template Ollama reports for
`FINE_TUNED_OLLAMA_MODEL` (`POST /api/show`) against
`chat_template.jinja` in `FINE_TUNED_ADAPTER_DIR`.

| `status` | Means |
|---|---|
| `match` | Serving reproduces the training template. This is the only passing state. |
| `mismatch` | Different templates. Quality degrades silently — rebuild the Ollama model. |
| `unknown` | One side could not be read (Ollama down, or no adapter fetched). **Not** a pass. |

`unknown` is deliberately distinct from `match`. Collapsing "we could not check"
into "it is fine" is how this file went on claiming an Unsloth Modelfile long
after the export producing it had been deleted.

## Tests

`backend/tests/test_serving_shim.py`, so they run with the normal `pytest`
command. They cover the request shape (messages not prompt, schema passed
through as the decoding constraint), the output guarantee — every rejection path
that stops an unparseable response reaching the application — the total-time
bound, and the template verdict including its `unknown` state.

They do **not** cover an end-to-end call against the real fine-tune. Nothing
does yet; that is Story 6.5 Task 7's remaining item.
