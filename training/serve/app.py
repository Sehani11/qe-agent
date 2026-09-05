"""HTTP serving shim for the fine-tuned BDD model — Story 6.5.

Implements exactly the contract `FineTunedModelProvider` sends, so the backend
can point `FINE_TUNED_MODEL_ENDPOINT` at this process and use the fine-tune with
no code change on its side.

    POST /
    {
      "acceptance_criteria": "AC1: ...",          # always sent
      "system_prompt": "You are an expert QA...", # sent when non-empty
      "response_format": { ...JSON schema... }    # sent when provided
    }
    -> 200 {"scenarios": [{source_ac_clause, feature, scenario, given, when, then}, ...]}

Three rules this file exists to enforce:

1. **The training chat template is used, not a hand-rolled prompt.** Requests go
   to Ollama's /api/chat with a `messages` array, so Ollama applies the served
   model's own template rather than a prompt string built here. Formatting the
   prompt by hand would silently degrade quality with no error to notice.

2. **That template is VERIFIED, not assumed.** AC6 requires serving to use the
   same template training used. The original plan got that from the Modelfile
   Unsloth exported next to the GGUF — but unsloth was dropped mid-story when it
   broke on Kaggle, and the export went with it. Nothing carries the template
   across automatically any more, so this shim compares the template Ollama
   reports (`/api/show`) against the one the run saved beside the adapter
   (`chat_template.jinja`) and surfaces the verdict on `/health`. A mismatch is
   the exact silent-degradation failure AC6 exists to prevent; it must be
   visible, not inferred.

3. **Nothing leaves this process unless it validates.** The response body is fed
   straight into `BDDGenerateResponse.model_validate()` by bdd_service, so a shim
   that can emit an unparseable shape just moves the failure downstream. It is
   validated here first.

Run it:
    uv run --project backend python -m uvicorn app:app --app-dir training/serve --port 9000

Then point the backend at it:
    BDD_MODEL_PROVIDER=fine_tuned
    FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
"""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import logging
import os
import re
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Same bootstrap as training/build_dataset.py: training/ sits outside the backend
# package, so put backend/ on the path and run from it (app.core.config resolves
# env_file against the CWD, and the repo-root .env holds keys Settings rejects).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
os.chdir(REPO_ROOT / "backend")

# Load BDDGenerateResponse BY PATH rather than `from app.schemas.bdd import ...`.
#
# The documented run command is `uvicorn app:app --app-dir training/serve`, which
# imports THIS file as the top-level module `app` — shadowing the backend package
# of the same name, so the package import resolves to this module and fails with
# "'app' is not a package". Loading by path sidesteps the collision entirely.
# `backend/app/schemas/bdd.py` imports only stdlib + pydantic, so it loads
# standalone with no package context. Pinned by
# test_shim_imports_under_the_module_name_uvicorn_gives_it.
_bdd_schema_spec = importlib.util.spec_from_file_location(
    "qe_bdd_schema", REPO_ROOT / "backend" / "app" / "schemas" / "bdd.py"
)
assert _bdd_schema_spec and _bdd_schema_spec.loader
_bdd_schema = importlib.util.module_from_spec(_bdd_schema_spec)
_bdd_schema_spec.loader.exec_module(_bdd_schema)
BDDGenerateResponse = _bdd_schema.BDDGenerateResponse

logger = logging.getLogger("bdd_shim")

# Uvicorn configures only its own loggers, so without a handler here every
# message this module logs is silently discarded. That included the startup
# chat-template verdict — the one warning that says the served model is not the
# one that was trained — which has never actually reached a console. Same
# approach as backend/app/main.py.
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# Read settings from backend/.env as well as the process environment, so the
# shim is configured in the same file as the backend it serves.
#
# Without this every start needed four `export` lines, and a forgotten one was
# silent: the shim came up on defaults, served the WRONG model, and the only
# clue was the model name buried in /health. A real export still wins —
# `override=False` — so a one-off run can still be tuned from the command line
# without editing the file.
_ENV_FILE = REPO_ROOT / "backend" / ".env"
if _ENV_FILE.exists():
    from dotenv import load_dotenv

    load_dotenv(_ENV_FILE, override=False)

# The model name registered in Ollama. Getting the fine-tune in there is a
# manual conversion step now that the Unsloth GGUF export is gone — see
# serve/README.md, "Getting the adapter into Ollama".
MODEL_NAME = os.environ.get("FINE_TUNED_OLLAMA_MODEL", "bdd-lora")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

# Must stay under the backend's FINE_TUNED_MODEL_TIMEOUT_SECONDS. The provider
# bounds the WHOLE exchange with asyncio.timeout and still has to run the
# general-LLM fallback afterwards inside NFR-P3's 30s budget, so in production a
# shim that hangs to the limit is worse than one that fails fast.
#
# The 10s default is the PRODUCTION value. Local research on non-GPU hardware
# needs far more — a generation has been measured at 175s — which is exactly
# why this is settable from backend/.env rather than hardcoded.
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("SHIM_TIMEOUT_SECONDS", "10"))

# Cap on generated tokens. Without one, Ollama generates until the context
# window is exhausted, so a single request can run for minutes and still return
# nothing usable — measured: an unnumbered 876-character ticket burned a full
# 300s timeout and produced ZERO scenarios, while a numbered 669-character one
# finished in 35s. Raising the timeout does not fix that; bounding the output
# does.
#
# 768 is `evaluation.sample_max_new_tokens` from train_config.yaml — the budget
# the training run itself used when sampling, so it is a length the model has
# been observed to complete a full answer within.
# Raised from 768 so the scenario cap below is REACHABLE. A cap the token
# budget cannot accommodate never binds: at ~100 tokens per scenario, 768 stops
# generation mid-string before any array limit applies, which is exactly the
# failure this pair of bounds exists to prevent.
MAX_OUTPUT_TOKENS = int(os.environ.get("SHIM_MAX_OUTPUT_TOKENS", "1536"))

# Ceiling on scenarios per answer, and the floor/ceiling for the per-request
# cap derived from the input.
MAX_SCENARIOS = int(os.environ.get("SHIM_MAX_SCENARIOS", "12"))
MIN_SCENARIOS = 3
#: Used when the criteria carry no numbered clauses. Kept deliberately low —
#: unnumbered prose is the input that sends this model into a repetition loop,
#: because there is no enumerated list to work through and therefore nothing
#: that says "done".
UNNUMBERED_SCENARIO_CAP = 5

_AC_CLAUSE_RE = re.compile(r"\bAC\s*(\d+)", re.IGNORECASE)


def scenario_cap(acceptance_criteria: str) -> int:
    """How many scenarios this input may legitimately produce.

    Twice the AC clause count, so a clause can reasonably yield a happy path
    and an error path, clamped either side. Without numbered clauses there is
    nothing to scale against, so a low fixed cap applies.
    """
    clauses = {int(n) for n in _AC_CLAUSE_RE.findall(acceptance_criteria or "")}
    if not clauses:
        return UNNUMBERED_SCENARIO_CAP
    return max(MIN_SCENARIOS, min(len(clauses) * 2, MAX_SCENARIOS))


def bound_scenarios(
    schema: dict[str, object] | None, cap: int
) -> dict[str, object] | None:
    """Return `schema` with a maximum length on its `scenarios` array.

    This is the bound that actually works. Ollama builds a decoding grammar
    from the schema, so `maxItems` makes an over-long answer UNREPRESENTABLE
    rather than merely truncated — verified against the model: an input that
    burned a 300s timeout and returned nothing finished in 22s with three valid
    scenarios once the array was bounded.

    Copied, never mutated in place: the caller's schema is
    `BDDGenerateResponse.model_json_schema()`, which pydantic may hand back as
    a shared object, and editing it would alter validation elsewhere.
    """
    if not schema:
        return schema

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    scenarios = properties.get("scenarios")
    if not isinstance(scenarios, dict) or scenarios.get("type") != "array":
        return schema

    bounded = copy.deepcopy(schema)
    bounded["properties"]["scenarios"]["maxItems"] = cap
    return bounded

# Where the training run saved the adapter and the template it trained with.
# Must match `output.adapter_dir` in training/config/train_config.yaml; the
# kaggle_run.py fetch lands it under training/outputs/.
CHAT_TEMPLATE_FILENAME = "chat_template.jinja"
ADAPTER_DIR = Path(
    os.environ.get(
        "FINE_TUNED_ADAPTER_DIR",
        str(REPO_ROOT / "training" / "outputs" / "outputs" / "bdd-lora"),
    )
)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Say the AC6 verdict once, loudly, at boot — nobody reads /health unprompted.

    Also primes the cache /health serves, so monitoring never triggers an
    outbound call to Ollama.
    """
    # Print the RESOLVED settings, not just the template verdict. Serving the
    # wrong model was previously silent — the shim started fine and only the
    # model name in /health gave it away.
    logger.info(
        "shim config: model=%s timeout=%ss max_tokens=%s ollama=%s adapter_dir=%s",
        MODEL_NAME,
        REQUEST_TIMEOUT_SECONDS,
        MAX_OUTPUT_TOKENS,
        OLLAMA_BASE_URL,
        ADAPTER_DIR,
    )

    verdict = await refresh_template_verdict()
    if verdict["verified"]:
        logger.info("chat template verified: %s", verdict["detail"])
    else:
        logger.warning(
            "chat template NOT verified (%s): %s", verdict["status"], verdict["detail"]
        )
    yield


app = FastAPI(title="Fine-tuned BDD model shim", version="1.1", lifespan=lifespan)


class GenerateRequest(BaseModel):
    """The payload FineTunedModelProvider sends. Only the first field is required."""

    acceptance_criteria: str
    system_prompt: str = ""
    response_format: dict[str, object] | None = Field(default=None)


def build_ollama_payload(
    acceptance_criteria: str,
    system_prompt: str = "",
    response_format: dict[str, object] | None = None,
    model: str = MODEL_NAME,
) -> dict[str, object]:
    """Build the Ollama /api/chat request.

    Uses `messages` rather than a flattened `prompt` string so Ollama applies
    the served model's chat template instead of one hand-built here. Whether
    that template is the one the model was fine-tuned with is a separate
    question, answered by `verify_chat_template()` — sending `messages` is
    necessary for AC6 but not sufficient.
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": acceptance_criteria})

    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            # Near-greedy: this is a structured-extraction task, not a creative one.
            "temperature": 0.2,
            # Bounds the WORK, where the timeout only bounds the WAIT. Without
            # it a request can consume the whole timeout and still return
            # nothing — the caller then cannot tell "the model is slow" from
            # "the model never stops".
            "num_predict": MAX_OUTPUT_TOKENS,
        },
    }

    # Ollama accepts a JSON schema for `format`, which constrains decoding far
    # more reliably than asking politely in the prompt. Fall back to plain "json"
    # when no schema was supplied.
    #
    # The schema is bounded first, so the grammar itself forbids an unbounded
    # run of scenarios rather than leaving the token cap to truncate one.
    payload["format"] = (
        bound_scenarios(response_format, scenario_cap(acceptance_criteria))
        if response_format
        else "json"
    )
    return payload


def extract_scenarios(raw: str) -> dict[str, object]:
    """Parse and validate model output into a BDDGenerateResponse-shaped dict.

    Raises ValueError with a specific reason; the caller maps that to a 502.
    """
    if not raw or not raw.strip():
        raise ValueError("model returned an empty response")

    # Constrained decoding should make this exact, but a stray code fence or a
    # leading apology is cheap to survive — the same tolerance ollama_provider
    # applies in the backend.
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        raise ValueError("model output contained no JSON object")

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output was not valid JSON: {exc.msg}") from exc

    try:
        validated = BDDGenerateResponse.model_validate(parsed)
    except Exception as exc:
        raise ValueError(f"model output did not match BDDGenerateResponse: {exc}") from exc

    # An empty array validates (the field defaults to []) and would reach the
    # application as a successful generation with nothing in it.
    if not validated.scenarios:
        raise ValueError("model returned zero scenarios")

    return validated.model_dump()


async def call_model(payload: dict[str, object]) -> str:
    """POST to Ollama and return the assistant message content.

    `asyncio.timeout` bounds the WHOLE exchange. httpx's own timeout is
    per-phase — connect, read, write and pool each get the full value — so
    passing it alone would allow roughly 2x REQUEST_TIMEOUT_SECONDS on a slow
    connect, blowing straight through the provider's 12s budget and taking the
    general-LLM fallback's room in NFR-P3 with it. The backend guards its call
    to us the same way (fine_tuned_provider.py); this is that guard's other half.
    """
    async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()

    content = data.get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise ValueError("Ollama returned a non-text message content")
    return content


def load_training_template() -> str | None:
    """The chat template the fine-tune was trained with, or None if absent."""
    path = ADAPTER_DIR / CHAT_TEMPLATE_FILENAME
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


async def fetch_served_template() -> str | None:
    """The chat template Ollama reports for the model we are about to serve."""
    async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/show", json={"model": MODEL_NAME}
            )
            response.raise_for_status()
            data = response.json()

    template = data.get("template")
    return template if isinstance(template, str) else None


#: Chat templates are written in different LANGUAGES on either side of this
#: comparison — transformers saves Jinja, Ollama's /api/show returns a Go
#: template. Their source text therefore never matches even when the model is
#: served exactly as it was trained. What decides whether serving reproduces
#: training is the set of special control tokens the template emits, which is
#: language-independent: Qwen/ChatML emits <|im_start|>/<|im_end|>, Llama 3
#: emits <|start_header_id|>/<|eot_id|>. That is what we compare.
#:
#: LIMITATION, stated rather than hidden: this only understands pipe-delimited
#: control tokens (ChatML, Llama, Mistral). Families that use bare angle
#: brackets — Gemma's <start_of_turn> — yield nothing here, and the verdict is
#: `unknown` rather than a mismatch. Broadening to `<[a-z_]+>` was considered
#: and rejected: Qwen's own template contains <tools> and <tool_call>, so a
#: generic pattern would compare tool-calling markup and produce false
#: mismatches on the family this actually serves.
SPECIAL_TOKEN_RE = re.compile(r"<\|[a-zA-Z0-9_.\-]+\|>")


def template_wire_format(source: str) -> frozenset[str]:
    """The special control tokens a chat template emits, as a comparable set."""
    return frozenset(SPECIAL_TOKEN_RE.findall(source))


def compare_templates(trained: str | None, served: str | None) -> dict[str, object]:
    """Verdict on whether serving reproduces the training chat template (AC6).

    Two comparisons, reported distinctly so `/health` never overclaims:

      * ``exact``      — byte-identical source. Only possible when both sides
                         are written in the same template language.
      * ``wire-format`` — different source, identical special-token skeleton.
                         This is the normal passing case against Ollama.

    Reports ``unknown`` rather than guessing when either side is missing.
    "We could not check" and "we checked and it matches" are different facts,
    and collapsing them is how the original Unsloth-Modelfile claim survived
    long after the export that backed it was deleted.
    """
    if trained is None:
        return {
            "verified": False,
            "status": "unknown",
            "comparison": "none",
            "detail": (
                f"No {CHAT_TEMPLATE_FILENAME} under {ADAPTER_DIR}. Fetch the run "
                "output (python training/kaggle_run.py --fetch) or set "
                "FINE_TUNED_ADAPTER_DIR."
            ),
        }
    if served is None:
        return {
            "verified": False,
            "status": "unknown",
            "comparison": "none",
            "detail": (
                f"Ollama did not report a template for '{MODEL_NAME}'. Is the "
                "model registered? See serve/README.md."
            ),
        }
    if trained.strip() == served.strip():
        return {
            "verified": True,
            "status": "match",
            "comparison": "exact",
            "detail": "Serving uses the training template, byte for byte.",
        }

    trained_tokens = template_wire_format(trained)
    served_tokens = template_wire_format(served)

    # Neither side uses pipe-delimited tokens, so this checker cannot read
    # either one. That is "could not check" — NOT "they differ". Reporting a
    # mismatch here would raise a false alarm on any correctly-served
    # Gemma-family model, and the detail would read "trained emits [], served
    # emits []", which is incoherent.
    if not trained_tokens and not served_tokens:
        return {
            "verified": False,
            "status": "unknown",
            "comparison": "none",
            "detail": (
                "Could not compare: neither template uses pipe-delimited "
                "control tokens (<|...|>), which is all this check understands. "
                "Verify the prompt format by hand for this model family."
            ),
        }

    # One side has tokens and the other does not, or the sets differ — that IS
    # a real disagreement about the wire format.
    if trained_tokens and trained_tokens == served_tokens:
        return {
            "verified": True,
            "status": "match",
            "comparison": "wire-format",
            "detail": (
                "Template source differs (Jinja vs Ollama's Go template) but "
                f"both emit the same control tokens: {sorted(trained_tokens)}. "
                "The prompt format the model sees is the one it trained on."
            ),
        }

    return {
        "verified": False,
        "status": "mismatch",
        "comparison": "wire-format",
        "detail": (
            f"'{MODEL_NAME}' is served with a DIFFERENT chat template than the "
            f"fine-tune was trained with — trained emits {sorted(trained_tokens)}, "
            f"served emits {sorted(served_tokens)}. Output quality degrades "
            "silently. See serve/README.md, 'Getting the adapter into Ollama'."
        ),
    }


async def verify_chat_template() -> dict[str, object]:
    """Run the AC6 template check, tolerating an unreachable Ollama."""
    try:
        served = await fetch_served_template()
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError):
        served = None
    return compare_templates(load_training_template(), served)


#: Last template verdict, computed at startup and reused. /health is polled by
#: monitoring, and re-verifying per request would make it issue an outbound
#: Ollama call bounded at REQUEST_TIMEOUT_SECONDS — slowest exactly when the
#: model is saturated, which on CPU means 45s at a time. Liveness must not
#: depend on the model backend being idle.
_template_verdict: dict[str, object] | None = None
_template_checked_at: str | None = None


async def refresh_template_verdict() -> dict[str, object]:
    """Re-run the AC6 check and cache the result."""
    global _template_verdict, _template_checked_at
    _template_verdict = await verify_chat_template()
    _template_checked_at = datetime.now(UTC).isoformat()
    return _template_verdict


@app.get("/health")
async def health(refresh: bool = False) -> dict[str, object]:
    """Liveness plus the configuration that is easy to get wrong.

    The template verdict is served from the startup check. Pass `?refresh=true`
    to re-verify against Ollama — do that after re-registering the model, not
    on a monitoring interval.
    """
    verdict = (
        await refresh_template_verdict()
        if refresh or _template_verdict is None
        else _template_verdict
    )
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "ollama": OLLAMA_BASE_URL,
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "adapter_dir": str(ADAPTER_DIR),
        # AC6: is serving actually using the template training used?
        "chat_template": verdict,
        "chat_template_checked_at": _template_checked_at,
    }


@app.post("/")
async def generate(request: GenerateRequest) -> JSONResponse:
    """Generate BDD scenarios for the given acceptance criteria."""
    payload = build_ollama_payload(
        acceptance_criteria=request.acceptance_criteria,
        system_prompt=request.system_prompt,
        response_format=request.response_format,
    )

    try:
        raw = await call_model(payload)
    except (httpx.HTTPError, TimeoutError) as exc:
        # Non-2xx here makes the provider fall back to the general LLM and log
        # reason=endpoint_error — which is the correct, visible degradation.
        # TimeoutError is asyncio.timeout firing: better we return 502 at 10s
        # than let the provider's own 12s bound expire with nothing to show.
        return JSONResponse(
            status_code=502,
            content={"error": "MODEL_BACKEND_ERROR", "message": type(exc).__name__},
        )

    try:
        return JSONResponse(status_code=200, content=extract_scenarios(raw))
    except ValueError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": "MODEL_OUTPUT_INVALID", "message": str(exc)},
        )
