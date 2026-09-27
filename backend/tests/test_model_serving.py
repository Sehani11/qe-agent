"""Publishing a trained adapter into the local model runtime.

The three failures this module exists to prevent are all silent ones — they
produce a model that runs and answers, just worse:

  * an adapter applied to the wrong base (refused by the BASE_MODELS table),
  * a Modelfile carrying a Jinja template Ollama reads as Go (never sent),
  * a publish that "succeeded" while the shim still verifies against the
    previous run's template (reported, not swallowed).

So most of what is pinned here is what the publish REFUSES to do, and what it
says when it refuses. A wrong message is a real defect: `serve_detail` is shown
verbatim and is the only thing the user has to act on.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

from app.models.training_run import (
    SERVE_FAILED,
    SERVE_PUBLISHING,
    SERVE_SERVED,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from app.services import fine_tuned_serving, model_serving_service
from app.services.model_serving_service import ModelServingError

_RealAsyncClient = httpx.AsyncClient

BASE_1_5B = "unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit"


def _adapter(tmp_path, base: str | None = BASE_1_5B):
    """A directory shaped like a fetched run output."""
    run = tmp_path / "run-abc123"
    run.mkdir()
    config: dict = {"r": 16}
    if base is not None:
        config["base_model_name_or_path"] = base
    (run / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    (run / "adapter_model.safetensors").write_bytes(b"not really weights")
    (run / "chat_template.jinja").write_text("{{ messages }}", encoding="utf-8")
    return run


# ---------------------------------------------------------------------------
# The base-model table
# ---------------------------------------------------------------------------


def test_a_known_base_yields_both_strings_it_needs(tmp_path):
    """Two different names come out of one: the converter takes a Hugging Face
    id, the Modelfile inherits from a runtime tag."""
    hf, tag = model_serving_service.base_models_for(_adapter(tmp_path))
    assert hf == "Qwen/Qwen2.5-1.5B-Instruct"
    assert tag == "qwen2.5:1.5b-instruct"


def test_an_unknown_base_is_refused_rather_than_guessed(tmp_path):
    """Applying an adapter to the wrong base degrades output instead of
    failing, and the chat-template check cannot see it — so a guess here would
    be undetectable. Refusing is the only safe answer."""
    adapter = _adapter(tmp_path, base="someone/Llama-3-8B-Instruct-bnb-4bit")
    with pytest.raises(ModelServingError) as exc:
        model_serving_service.base_models_for(adapter)
    assert "not a known base model" in exc.value.message
    assert "BASE_MODELS" in exc.value.message


def test_the_base_lookup_is_case_insensitive(tmp_path):
    adapter = _adapter(tmp_path, base=BASE_1_5B.upper())
    assert model_serving_service.base_models_for(adapter)[1] == "qwen2.5:1.5b-instruct"


def test_an_adapter_with_no_config_says_so(tmp_path):
    bare = tmp_path / "run-empty"
    bare.mkdir()
    with pytest.raises(ModelServingError) as exc:
        model_serving_service.base_models_for(bare)
    assert "adapter_config.json" in exc.value.message


def test_a_config_naming_no_base_is_refused(tmp_path):
    with pytest.raises(ModelServingError) as exc:
        model_serving_service.base_models_for(_adapter(tmp_path, base=None))
    assert "does not name a base model" in exc.value.message


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


@pytest.fixture
def toolchain(monkeypatch):
    """Pretend the converter and its venv are installed."""
    monkeypatch.setattr(model_serving_service, "toolchain_missing", lambda: None)


async def test_readiness_reports_a_missing_converter_first(monkeypatch):
    """Before anything on the network: the other checks cost a round trip and
    cannot change the answer."""
    monkeypatch.setattr(
        model_serving_service, "toolchain_missing", lambda: "converter gone"
    )
    assert await model_serving_service.serving_ready() == "converter gone"


async def test_readiness_reports_a_shim_that_is_down(monkeypatch, toolchain):
    async def no_shim():
        return None

    monkeypatch.setattr(fine_tuned_serving, "health", no_shim)
    reason = await model_serving_service.serving_ready()
    assert "not responding" in reason


async def test_readiness_reports_a_shim_that_names_no_runtime(monkeypatch, toolchain):
    async def health():
        return {"model": "bdd-lora", "ollama": ""}

    monkeypatch.setattr(fine_tuned_serving, "health", health)
    reason = await model_serving_service.serving_ready()
    assert "nowhere to register" in reason


async def test_readiness_passes_when_everything_answers(monkeypatch, toolchain):
    async def health():
        return {"model": "bdd-lora", "ollama": "http://runtime.test:11434"}

    monkeypatch.setattr(fine_tuned_serving, "health", health)
    monkeypatch.setattr(
        model_serving_service.httpx,
        "AsyncClient",
        lambda **_: _RealAsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"version": "0.32.0"})
            )
        ),
    )
    assert await model_serving_service.serving_ready() is None


# ---------------------------------------------------------------------------
# Registering with the runtime
# ---------------------------------------------------------------------------


async def test_registering_sends_the_base_and_no_template(monkeypatch, tmp_path):
    """The Modelfile equivalent is two things: the base to inherit from, and
    the adapter. NOT a template — chat_template.jinja is Jinja and Ollama reads
    Go, so sending it would break the very prompt format it describes."""
    gguf = tmp_path / "bdd-lora-f16.gguf"
    gguf.write_bytes(b"gguf bytes")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "success"})

    monkeypatch.setattr(
        model_serving_service.httpx,
        "AsyncClient",
        lambda **_: _RealAsyncClient(transport=httpx.MockTransport(handler)),
    )

    await model_serving_service._register(
        "http://runtime.test:11434", "bdd-lora", "qwen2.5:1.5b-instruct", gguf
    )

    blob, create = seen
    assert blob.url.path.startswith("/api/blobs/sha256:")
    body = json.loads(create.content)
    assert body["model"] == "bdd-lora"
    assert body["from"] == "qwen2.5:1.5b-instruct"
    assert next(iter(body["adapters"].values())).startswith("sha256:")
    assert "template" not in body


async def test_a_runtime_refusal_is_reported_not_raised_raw(monkeypatch, tmp_path):
    gguf = tmp_path / "x.gguf"
    gguf.write_bytes(b"x")
    monkeypatch.setattr(
        model_serving_service.httpx,
        "AsyncClient",
        lambda **_: _RealAsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, text="unknown base model")
            )
        ),
    )
    with pytest.raises(ModelServingError) as exc:
        await model_serving_service._register(
            "http://runtime.test:11434", "m", "base", gguf
        )
    assert "unknown base model" in exc.value.message


# ---------------------------------------------------------------------------
# Starting a publish
# ---------------------------------------------------------------------------


class _Run:
    """The few fields `start_publish` reads, without a database."""

    def __init__(self, **fields):
        self.id = "run-1"
        self.status = STATUS_COMPLETED
        self.adapter_dir = "training/outputs/run-abc123"
        self.serve_status = None
        self.serve_detail = None
        self.served_model = None
        self.__dict__.update(fields)


class _Session:
    async def commit(self):
        pass

    async def refresh(self, _):
        pass


async def test_an_unfinished_run_cannot_be_published():
    with pytest.raises(ModelServingError) as exc:
        await model_serving_service.start_publish(
            _Run(status=STATUS_FAILED), _Session()
        )
    assert "completed run" in exc.value.message


async def test_a_run_with_no_adapter_cannot_be_published():
    with pytest.raises(ModelServingError) as exc:
        await model_serving_service.start_publish(_Run(adapter_dir=None), _Session())
    assert "did not produce an adapter" in exc.value.message


async def test_a_publish_already_running_is_not_started_twice():
    with pytest.raises(ModelServingError) as exc:
        await model_serving_service.start_publish(
            _Run(serve_status=SERVE_PUBLISHING), _Session()
        )
    assert "already being published" in exc.value.message


async def test_the_readiness_reason_is_what_the_caller_sees(monkeypatch):
    """Refused before the row is touched, so the button can show the reason
    instead of a row that turns red a minute later."""

    async def blocked():
        return "The model runtime at http://runtime.test is not responding."

    monkeypatch.setattr(model_serving_service, "serving_ready", blocked)
    run = _Run()
    with pytest.raises(ModelServingError) as exc:
        await model_serving_service.start_publish(run, _Session())
    assert "not responding" in exc.value.message
    assert run.serve_status is None


async def test_starting_marks_the_row_and_detaches_a_worker(monkeypatch):
    async def ready():
        return None

    started: list = []

    async def fake_publish(run_id, adapter_dir):
        started.append((run_id, adapter_dir))

    monkeypatch.setattr(model_serving_service, "serving_ready", ready)
    monkeypatch.setattr(model_serving_service, "_publish", fake_publish)

    run = await model_serving_service.start_publish(_Run(), _Session())
    assert run.serve_status == SERVE_PUBLISHING
    assert run.serve_detail == "Queued"

    # The worker is a detached task, so it has not necessarily run yet; drain it.
    for task in list(model_serving_service._running):
        await task
    assert started == [("run-1", "training/outputs/run-abc123")]


# ---------------------------------------------------------------------------
# The publish itself
# ---------------------------------------------------------------------------


@pytest.fixture
def publish_env(monkeypatch, tmp_path):
    """Everything `_publish` reaches for, with the slow parts stubbed."""
    adapter = _adapter(tmp_path)
    written: dict = {}

    async def health():
        return {"model": "bdd-lora-1.5b", "ollama": "http://runtime.test:11434"}

    async def update(run_id, **fields):
        written.update(fields)

    monkeypatch.setattr(fine_tuned_serving, "health", health)
    monkeypatch.setattr(model_serving_service, "_update", update)
    monkeypatch.setattr(
        model_serving_service, "resolved_adapter", lambda _: adapter
    )
    return {"adapter": adapter, "written": written}


async def test_a_missing_base_in_the_runtime_names_the_pull_command(
    monkeypatch, publish_env
):
    """The adapter is useless without the base it trained on, and `ollama pull`
    is the fix — so the message is the command rather than a description."""

    async def absent(runtime, tag):
        return False

    monkeypatch.setattr(fine_tuned_serving, "model_exists", absent)

    await model_serving_service._publish("run-1", "training/outputs/run-abc123")

    written = publish_env["written"]
    assert written["serve_status"] == SERVE_FAILED
    assert "ollama pull qwen2.5:1.5b-instruct" in written["serve_detail"]


async def test_a_successful_publish_records_the_served_name(monkeypatch, publish_env):
    async def present(runtime, tag):
        return True

    async def convert(adapter, hf_base, outfile):
        outfile.write_bytes(b"gguf")

    async def register(runtime, model, base_tag, gguf):
        pass

    async def point(adapter):
        return None

    monkeypatch.setattr(fine_tuned_serving, "model_exists", present)
    monkeypatch.setattr(model_serving_service, "_convert", convert)
    monkeypatch.setattr(model_serving_service, "_register", register)
    monkeypatch.setattr(model_serving_service, "_point_shim_at", point)

    await model_serving_service._publish("run-1", "training/outputs/run-abc123")

    written = publish_env["written"]
    assert written["serve_status"] == SERVE_SERVED
    assert written["served_model"] == "bdd-lora-1.5b"
    assert written["serve_detail"] == "Serving bdd-lora-1.5b from this run."


async def test_a_stale_template_is_reported_on_an_otherwise_good_publish(
    monkeypatch, publish_env
):
    """The model IS registered and served by this point, so a shim that could
    not be repointed is a warning on a success — not a failure the user would
    retry."""

    async def present(runtime, tag):
        return True

    async def convert(adapter, hf_base, outfile):
        outfile.write_bytes(b"gguf")

    async def register(runtime, model, base_tag, gguf):
        pass

    async def point(adapter):
        return " The serving shim still verifies against the previous run."

    monkeypatch.setattr(fine_tuned_serving, "model_exists", present)
    monkeypatch.setattr(model_serving_service, "_convert", convert)
    monkeypatch.setattr(model_serving_service, "_register", register)
    monkeypatch.setattr(model_serving_service, "_point_shim_at", point)

    await model_serving_service._publish("run-1", "training/outputs/run-abc123")

    written = publish_env["written"]
    assert written["serve_status"] == SERVE_SERVED
    assert "still verifies against the previous run" in written["serve_detail"]


async def test_a_conversion_failure_lands_as_a_readable_reason(
    monkeypatch, publish_env
):
    async def present(runtime, tag):
        return True

    async def convert(adapter, hf_base, outfile):
        raise ModelServingError("Converting the adapter failed: no module named gguf")

    monkeypatch.setattr(fine_tuned_serving, "model_exists", present)
    monkeypatch.setattr(model_serving_service, "_convert", convert)

    await model_serving_service._publish("run-1", "training/outputs/run-abc123")

    written = publish_env["written"]
    assert written["serve_status"] == SERVE_FAILED
    assert "no module named gguf" in written["serve_detail"]


async def test_an_adapter_gone_from_disk_is_not_a_crash(monkeypatch, publish_env):
    monkeypatch.setattr(model_serving_service, "resolved_adapter", lambda _: None)

    await model_serving_service._publish("run-1", "training/outputs/gone")

    written = publish_env["written"]
    assert written["serve_status"] == SERVE_FAILED
    assert "no longer on disk" in written["serve_detail"]


# ---------------------------------------------------------------------------
# The converter subprocess
# ---------------------------------------------------------------------------
#
# These run a REAL child process on purpose. The first publish from the UI
# failed because the converter was spawned with asyncio, which on Windows only
# works from a Proactor event loop while uvicorn runs a selector one — a
# failure that cannot appear if the subprocess is mocked away.

WRITES_A_FILE = """
import sys
out = sys.argv[sys.argv.index('--outfile') + 1]
open(out, 'wb').write(b'gguf')
"""

FAILS_LOUDLY = """
import sys
print('loading tensors')
print('ERROR: unsupported architecture', file=sys.stderr)
sys.exit(1)
"""

WRITES_NOTHING = "pass"


def _use_fake_converter(monkeypatch, tmp_path, body: str):
    """Point the converter at a script we control, run by this interpreter."""
    script = tmp_path / "fake_convert.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(model_serving_service, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(model_serving_service, "CONVERTER_SCRIPT", script.name)
    monkeypatch.setattr(
        model_serving_service, "_convert_python", lambda: Path(sys.executable)
    )


async def test_the_converter_runs_off_the_event_loop(monkeypatch, tmp_path):
    """A real child process, started from async code, writing a real file."""
    _use_fake_converter(monkeypatch, tmp_path, WRITES_A_FILE)
    outfile = tmp_path / "out.gguf"

    await model_serving_service._convert(
        tmp_path, "Qwen/Qwen2.5-1.5B-Instruct", outfile
    )

    assert outfile.read_bytes() == b"gguf"


async def test_a_failing_converter_reports_the_error_not_the_tail(
    monkeypatch, tmp_path
):
    """The reason, wherever it landed in the merged stream.

    Piped stdout is block-buffered and stderr is not, so a child that prints
    progress and then dies delivers them out of order: taking the last line
    reported "loading tensors" for a failure caused two lines earlier.
    """
    _use_fake_converter(monkeypatch, tmp_path, FAILS_LOUDLY)

    with pytest.raises(ModelServingError) as exc:
        await model_serving_service._convert(
            tmp_path, "Qwen/Qwen2.5-1.5B-Instruct", tmp_path / "out.gguf"
        )

    assert "unsupported architecture" in exc.value.message


async def test_a_converter_that_writes_nothing_is_not_a_success(monkeypatch, tmp_path):
    """Exit 0 is not the same as a GGUF on disk, and registering a file that is
    not there fails later and less clearly."""
    _use_fake_converter(monkeypatch, tmp_path, WRITES_NOTHING)

    with pytest.raises(ModelServingError) as exc:
        await model_serving_service._convert(
            tmp_path, "Qwen/Qwen2.5-1.5B-Instruct", tmp_path / "out.gguf"
        )

    assert "wrote no file" in exc.value.message


async def test_a_converter_that_cannot_start_says_so(monkeypatch, tmp_path):
    """Distinct from a conversion failure: nothing ran at all."""
    monkeypatch.setattr(model_serving_service, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        model_serving_service, "_convert_python", lambda: tmp_path / "no-such-python"
    )

    with pytest.raises(ModelServingError) as exc:
        await model_serving_service._convert(
            tmp_path, "Qwen/Qwen2.5-1.5B-Instruct", tmp_path / "out.gguf"
        )

    assert "could not be started" in exc.value.message


def test_output_with_no_error_line_falls_back_to_the_tail():
    plain = """one
two
three"""
    assert model_serving_service._failure_reason(plain) == "three"


def test_an_error_line_wins_over_later_noise():
    noisy = """ERROR: unsupported architecture
loading tensors"""
    assert (
        "unsupported architecture"
        in model_serving_service._failure_reason(noisy)
    )


def test_empty_output_still_says_something():
    assert model_serving_service._failure_reason("   ") == "no output"
