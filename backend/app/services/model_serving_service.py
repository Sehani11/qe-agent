"""Publish a finished run's adapter to the local model runtime.

Training stops at a PEFT adapter. Getting that adapter served was a manual
three-step documented in `training/serve/README.md` — convert it to GGUF with
llama.cpp, write a two-line Modelfile, register it with Ollama — and every step
has a way to be subtly wrong that produces a model which *runs* and quietly
generates worse output. This module is that sequence, done once, from the app.

**Local-only by construction.** The converter is a script in an isolated venv
beside the repo and the runtime is reached at a host address; a deployed backend
has neither. `serving_ready()` is what lets the UI say so instead of offering a
button that always fails — the same shape as `kaggle_ready()` next door.

The three things most worth knowing, all learned the hard way in serve/README:

* **The base must match what the adapter trained on.** A LoRA applied to a
  different base produces erratic output rather than an error, so the mapping
  below is an explicit table and an unknown base is refused.
* **The Modelfile must NOT carry a TEMPLATE line.** `chat_template.jinja` is
  Jinja; Ollama Modelfiles take Go templates. Pasting one in breaks the prompt
  format — the exact failure the template check exists to catch. The base's own
  ChatML template is inherited instead.
* **The model name comes from the shim, not from us.** Registering under the
  name the shim already serves is what makes this one click: nothing has to be
  reconfigured afterwards. Asking rather than guessing is the same rule
  `purge_served_model` follows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.training_run import (
    SERVE_FAILED,
    SERVE_PUBLISHING,
    SERVE_SERVED,
    STATUS_COMPLETED,
    TrainingRun,
)
from app.services import fine_tuned_serving
from app.services.training_run_service import repo_root, resolved_adapter

logger = logging.getLogger(__name__)


class ModelServingError(Exception):
    """Raised when a publish cannot be started or cannot continue.

    `message` is written straight to `serve_detail` and shown to the user, so
    every one of them names the thing to do next.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# --- The base-model table ----------------------------------------------------
#
# Keyed by `base_model_name_or_path` from adapter_config.json, lowercased. Each
# entry gives the two DIFFERENT strings the publish needs: the Hugging Face id
# the converter fetches config and tokenizer from, and the runtime tag the
# Modelfile inherits from.
#
# A table and not a regex, deliberately. The mapping from
# "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit" to "qwen2.5:1.5b-instruct" is a
# naming convention, not a rule, and a wrong guess does not fail — it serves the
# adapter on the wrong base, which degrades output silently and which the
# chat-template check cannot detect (it compares prompt formats, not weights).
# Refusing an unknown base costs one line here and removes that whole class of
# bad result.
BASE_MODELS: dict[str, tuple[str, str]] = {
    "unsloth/qwen2.5-1.5b-instruct-bnb-4bit": (
        "Qwen/Qwen2.5-1.5B-Instruct",
        "qwen2.5:1.5b-instruct",
    ),
    "unsloth/qwen2.5-7b-instruct-bnb-4bit": (
        "Qwen/Qwen2.5-7B-Instruct",
        "qwen2.5:7b-instruct",
    ),
    "qwen/qwen2.5-1.5b-instruct": (
        "Qwen/Qwen2.5-1.5B-Instruct",
        "qwen2.5:1.5b-instruct",
    ),
    "qwen/qwen2.5-7b-instruct": (
        "Qwen/Qwen2.5-7B-Instruct",
        "qwen2.5:7b-instruct",
    ),
}

#: Where the conversion toolchain lives, relative to the repo root. Both are set
#: up by hand (serve/README.md): llama.cpp is a clone, and the venv pins
#: numpy~=1.26.4, which is why it is isolated and must never be backend/.venv.
CONVERTER_SCRIPT = Path("llama.cpp") / "convert_lora_to_gguf.py"
CONVERT_VENV = Path("convert-venv")

#: Conversion is CPU-bound and measured at well under a minute for a 1.5B
#: adapter, but a 7B one is several times that. Generous, because the cost of
#: being wrong is a half-finished GGUF.
_CONVERT_TIMEOUT_SECONDS = 900.0
#: Blob upload of a ~40 MB file to a local runtime, plus manifest write.
_RUNTIME_TIMEOUT_SECONDS = 300.0

#: Tasks are held so the event loop does not collect a publish mid-flight; the
#: same reason `training_run_service` keeps its own set.
_running: set[asyncio.Task] = set()


def _convert_python() -> Path:
    """The isolated converter venv's interpreter, per platform layout."""
    venv = repo_root() / CONVERT_VENV
    windows = venv / "Scripts" / "python.exe"
    return windows if windows.exists() else venv / "bin" / "python"


def toolchain_missing() -> str | None:
    """Return None when the converter is usable here, or what to install.

    Named for what it checks rather than for a verdict: the runtime is a
    separate question, asked over the network in `serving_ready()`.
    """
    if not (repo_root() / CONVERTER_SCRIPT).exists():
        return (
            "The GGUF converter is not available on this server. It comes from "
            "llama.cpp, which is cloned next to the repo: "
            "git clone --depth 1 https://github.com/ggml-org/llama.cpp"
        )
    if not _convert_python().exists():
        return (
            "The converter's Python environment is missing. It is deliberately "
            "separate from the backend's (it pins numpy~=1.26.4) — see "
            "training/serve/README.md for the uv venv command that builds it."
        )
    return None


async def serving_ready() -> str | None:
    """Return None when a run can be published here, or what blocks it.

    Three things have to hold, and they fail in this order because each later
    check is meaningless without the earlier one: the converter exists, the
    shim is up and names a runtime, and that runtime answers.
    """
    missing = toolchain_missing()
    if missing:
        return missing

    # The shim is asked first because it owns both the name to register under
    # and the address of the runtime to register with. Without it this module
    # would be guessing at both.
    health = await fine_tuned_serving.health()
    if health is None:
        return (
            "The fine-tuned model service is not responding, so there is "
            "nothing to publish into. Start the serving shim on its port and "
            "try again."
        )
    if not isinstance(health.get("model"), str) or not health["model"]:
        return "The fine-tuned model service is not serving a model name."
    runtime = health.get("ollama")
    if not isinstance(runtime, str) or not runtime:
        return (
            "The fine-tuned model service did not report a model runtime, so "
            "there is nowhere to register the adapter."
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.get(f"{runtime.rstrip('/')}/api/version")
    except httpx.HTTPError:
        return f"The model runtime at {runtime} is not responding."
    return None


def base_models_for(adapter_dir: Path) -> tuple[str, str]:
    """The (Hugging Face id, runtime tag) the adapter in `adapter_dir` needs."""
    config = adapter_dir / "adapter_config.json"
    if not config.exists():
        raise ModelServingError(
            f"No adapter_config.json in {adapter_dir.name}, so the base model "
            "it was trained on cannot be determined."
        )
    try:
        base = json.loads(config.read_text(encoding="utf-8")).get(
            "base_model_name_or_path"
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelServingError(
            f"adapter_config.json in {adapter_dir.name} could not be read."
        ) from exc

    if not isinstance(base, str) or not base:
        raise ModelServingError(
            f"adapter_config.json in {adapter_dir.name} does not name a base "
            "model."
        )
    known = BASE_MODELS.get(base.lower())
    if known is None:
        raise ModelServingError(
            f"{base} is not a known base model. Applying an adapter to the "
            "wrong base produces bad output rather than an error, so it is "
            "refused — add it to BASE_MODELS in model_serving_service.py with "
            "its Hugging Face id and runtime tag."
        )
    return known


async def _update(run_id, **fields) -> None:
    """Write publish progress in its own short-lived session.

    Same reasoning as the training worker's: a publish spends its life in a
    subprocess, and a connection held across that is closed by the pooler.
    """
    async with async_session_factory() as db:
        run = await db.get(TrainingRun, run_id)
        if run is None:  # pragma: no cover - the row is read before this
            return
        for key, value in fields.items():
            setattr(run, key, value)
        await db.commit()


#: Words that mark the line worth showing. The converter's own failures are
#: Python tracebacks and logging calls, so this is deliberately broad.
_ERROR_MARKERS = ("error", "exception", "traceback", "failed", "not found")


def _failure_reason(output: str) -> str:
    """The one line of converter output worth putting in front of a user.

    NOT simply the last line. stdout is block-buffered when piped while stderr
    is not, so a child that prints progress and then dies writes them to the
    merged pipe in the WRONG order — the reason first, the last progress line
    last. Taking the tail reported "loading tensors" for a failure whose actual
    cause was two lines above it.

    So the last error-shaped line wins, and the tail is only the fallback for
    output that has no such line at all.
    """
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "no output"
    for line in reversed(lines):
        if any(marker in line.lower() for marker in _ERROR_MARKERS):
            return line[:300]
    return lines[-1][:300]


def _run_converter(adapter: Path, hf_base: str, outfile: Path) -> tuple[int, str]:
    """Run the converter to completion on a worker thread.

    Blocking `subprocess.run`, NOT `asyncio.create_subprocess_exec`, for the
    reason `training_run_service._pump` spells out: asyncio can only spawn a
    process from a Proactor event loop on Windows, and uvicorn runs a
    `_WindowsSelectorEventLoop` where every `create_subprocess_exec` raises a
    bare `NotImplementedError`. The loop belongs to the server, so the process
    has to start somewhere that does not care which loop is running.

    This is not hypothetical — it is what the first publish from the UI hit,
    and because `NotImplementedError` is not a `ModelServingError` it surfaced
    as "failed unexpectedly" rather than as anything actionable.

    stderr is merged into stdout: the converter writes progress to one and the
    reason it stopped to the other, and reading a failure needs both in the
    order they happened.
    """
    completed = subprocess.run(
        [
            str(_convert_python()),
            str(repo_root() / CONVERTER_SCRIPT),
            str(adapter),
            "--base-model-id",
            hf_base,
            "--outfile",
            str(outfile),
            "--outtype",
            "f16",
        ],
        cwd=str(repo_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_CONVERT_TIMEOUT_SECONDS,
        check=False,
        # A GUI-less parent would otherwise flash a console window.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode, completed.stdout or ""


async def _convert(adapter: Path, hf_base: str, outfile: Path) -> None:
    """Adapter -> GGUF, in the isolated converter venv."""
    try:
        code, output = await asyncio.to_thread(
            _run_converter, adapter, hf_base, outfile
        )
    except subprocess.TimeoutExpired as exc:
        raise ModelServingError(
            f"Converting the adapter took longer than "
            f"{int(_CONVERT_TIMEOUT_SECONDS)}s and was stopped."
        ) from exc
    except OSError as exc:
        # The converter venv exists but will not start — a wrong architecture,
        # a broken install. Worth naming, because it looks identical to a
        # conversion failure from the outside.
        raise ModelServingError(
            f"The converter could not be started: {exc.strerror or exc}"
        ) from exc

    if code != 0:
        # The whole output goes to the log; one line of it goes to the user.
        logger.warning("Adapter conversion failed (%s): %s", code, output.strip())
        raise ModelServingError(
            f"Converting the adapter failed: {_failure_reason(output)}"
        )

    if not outfile.exists():
        raise ModelServingError("The converter reported success but wrote no file.")


async def _register(runtime: str, model: str, base_tag: str, gguf: Path) -> None:
    """Upload the GGUF and register it under `model` in the runtime.

    Over HTTP rather than the `ollama` CLI, so publishing needs nothing on PATH
    — the same interface `purge_served_model` already deletes through. The blob
    is content-addressed, so re-publishing the same adapter re-uses it.

    No TEMPLATE is sent: the adapter inherits the base's, which is the whole
    reason the base tag has to be right. See the module docstring.
    """
    digest = "sha256:" + hashlib.sha256(gguf.read_bytes()).hexdigest()
    base = runtime.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_RUNTIME_TIMEOUT_SECONDS) as client:
            upload = await client.post(
                f"{base}/api/blobs/{digest}", content=gguf.read_bytes()
            )
            upload.raise_for_status()

            created = await client.post(
                f"{base}/api/create",
                json={
                    "model": model,
                    "from": base_tag,
                    "adapters": {gguf.name: digest},
                    "stream": False,
                },
            )
            created.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()[:200] or exc.response.reason_phrase
        raise ModelServingError(
            f"The model runtime refused the adapter: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ModelServingError(
            f"The model runtime could not be reached: {type(exc).__name__}"
        ) from exc


async def _base_present(runtime: str, tag: str) -> bool:
    """Whether the runtime already holds the base the Modelfile inherits from."""
    return await fine_tuned_serving.model_exists(runtime, tag) is True


async def _point_shim_at(adapter: Path) -> str | None:
    """Tell the shim to verify against this run's template. Returns a warning.

    Best-effort by design: the model is already registered and being served by
    the time this runs, so a shim that refuses leaves a WORKING publish with a
    stale template verdict. That is worth a sentence in `serve_detail`, not a
    failed publish the user might retry.
    """
    endpoint = (settings.fine_tuned_model_endpoint or "").strip()
    if not endpoint:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/adapter",
                json={"adapter_dir": str(adapter)},
            )
            response.raise_for_status()
            verdict = response.json().get("chat_template") or {}
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Could not repoint the shim: %s", type(exc).__name__)
        return (
            " The serving shim still verifies against the previous run's "
            "template; restart it to clear that."
        )

    if verdict.get("status") != "match":
        # Not a failure of the publish, but the one thing that silently
        # degrades output, so it is said out loud rather than left in /health.
        return (
            f" Chat template check: {verdict.get('status')} — "
            f"{verdict.get('detail', 'see the shim log')}"
        )
    return None


async def _publish(run_id, adapter_dir: str) -> None:
    """The publish itself. Never raises: every end writes a terminal state."""
    try:
        health = await fine_tuned_serving.health()
        if health is None:
            raise ModelServingError(
                "The fine-tuned model service stopped responding before the "
                "adapter could be published."
            )
        model = health.get("model")
        runtime = health.get("ollama")
        if not isinstance(model, str) or not model:
            raise ModelServingError(
                "The fine-tuned model service is not serving a model name, so "
                "there is no name to register under."
            )
        if not isinstance(runtime, str) or not runtime:
            raise ModelServingError(
                "The fine-tuned model service did not report a model runtime."
            )

        adapter = resolved_adapter(adapter_dir)
        if adapter is None or not adapter.exists():
            raise ModelServingError(
                "This run's adapter is no longer on disk, so there is nothing "
                "to publish."
            )

        hf_base, base_tag = base_models_for(adapter)
        if not await _base_present(runtime, base_tag):
            raise ModelServingError(
                f"The base model {base_tag} is not in the runtime. The adapter "
                f"is trained on it and cannot be served without it: run "
                f"`ollama pull {base_tag}`."
            )

        await _update(
            run_id,
            serve_detail=f"Converting the adapter for {base_tag}…",
        )
        gguf = adapter.parent / f"{model}-f16.gguf"
        await _convert(adapter, hf_base, gguf)

        await _update(run_id, serve_detail=f"Registering {model} with the runtime…")
        await _register(runtime, model, base_tag, gguf)

        warning = await _point_shim_at(adapter)
        await _update(
            run_id,
            serve_status=SERVE_SERVED,
            served_model=model,
            serve_detail=f"Serving {model} from this run.{warning or ''}",
        )
        logger.info("Published run %s as %s", run_id, model)

    except ModelServingError as exc:
        await _update(run_id, serve_status=SERVE_FAILED, serve_detail=exc.message)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected failure publishing run %s", run_id)
        # The type at least distinguishes "the converter is missing" from "the
        # runtime refused" without shell access. The full traceback stays in
        # the log; a message shown in the UI must not carry one.
        await _update(
            run_id,
            serve_status=SERVE_FAILED,
            serve_detail=(
                f"The publish failed unexpectedly ({type(exc).__name__}). "
                "The server log has the detail."
            ),
        )


async def start_publish(run: TrainingRun, db: AsyncSession) -> TrainingRun:
    """Mark a run as publishing and detach a worker to do it.

    Refuses on anything that would make the work pointless before it starts, so
    the button's failure arrives as an HTTP error the client can show rather
    than as a row that turns red a minute later.
    """
    if run.status != STATUS_COMPLETED:
        raise ModelServingError(
            "Only a completed run has an adapter to publish."
        )
    if not run.adapter_dir:
        raise ModelServingError("This run did not produce an adapter.")
    if run.serve_status == SERVE_PUBLISHING:
        raise ModelServingError("This run is already being published.")

    blocked = await serving_ready()
    if blocked:
        raise ModelServingError(blocked)

    run.serve_status = SERVE_PUBLISHING
    run.serve_detail = "Queued"
    run.served_model = None
    await db.commit()
    await db.refresh(run)

    task = asyncio.create_task(_publish(run.id, run.adapter_dir))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return run
