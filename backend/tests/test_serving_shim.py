"""Tests for the fine-tuned model serving shim (Story 6.5).

Lives under backend/tests so it runs with the normal `pytest` command —
`training/` sits outside the backend package, and a suite that needs its own
invocation is one nobody remembers to run (Story 6.6 review finding).

The shim's job is to satisfy the contract `FineTunedModelProvider` sends and to
guarantee that nothing leaves it which `BDDGenerateResponse` cannot parse. Both
are pinned here.
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path

import httpx
import pytest

_SERVE_DIR = Path(__file__).resolve().parents[2] / "training" / "serve"


def _load_shim():
    """Import training/serve/app.py by path (it is not an installed package)."""
    cwd = Path.cwd()
    if str(_SERVE_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVE_DIR))
    spec = importlib.util.spec_from_file_location("bdd_shim", _SERVE_DIR / "app.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # The shim chdirs into backend/ on import, mirroring build_dataset.py.
        # Restore so test ordering cannot be affected.
        import os

        os.chdir(cwd)
    return module


shim = _load_shim()


def test_shim_imports_under_the_module_name_uvicorn_gives_it():
    """The documented run command imports this file AS `app`.

        uvicorn app:app --app-dir training/serve

    That binds sys.modules["app"] to the shim, which then shadows the backend's
    own `app` package — so a plain `from app.schemas.bdd import ...` inside the
    shim resolves to the shim itself and dies with "'app' is not a package".

    The other tests load the file as "bdd_shim", which sidesteps the collision
    entirely and is why this went unnoticed until Story 6.2 first ran the
    documented command. Reproduce the real import name here.

    Must run in a COLD interpreter: by the time pytest gets here the suite has
    already imported the real `app.schemas.bdd`, so an in-process attempt finds
    it cached in sys.modules and passes for the wrong reason.
    """
    import subprocess

    shim_path = _SERVE_DIR / "app.py"
    probe = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('app', r'{shim_path}')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "sys.modules['app'] = m\n"
        "spec.loader.exec_module(m)\n"
        "assert m.BDDGenerateResponse is not None\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, (
        "Shim cannot be imported as `app` — the documented uvicorn command "
        f"will fail:\n{result.stderr[-1500:]}"
    )


def _valid_model_output() -> str:
    return json.dumps(
        {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "Password reset",
                    "scenario": "A user requests a reset link",
                    "given": "a registered user with a verified email address",
                    "when": "they submit the password reset form",
                    "then": "a reset link is emailed to their address",
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# The request the shim sends to the model backend
# ---------------------------------------------------------------------------


def test_payload_uses_messages_so_the_training_template_is_applied():
    """A hand-built prompt string would drift from the fine-tune's template."""
    payload = shim.build_ollama_payload("AC1: reset password", system_prompt="sys")

    assert "messages" in payload
    assert "prompt" not in payload
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "AC1: reset password"},
    ]


def test_an_empty_system_prompt_is_omitted_not_sent_blank():
    payload = shim.build_ollama_payload("AC1: reset password")

    assert [m["role"] for m in payload["messages"]] == ["user"]


def test_a_response_schema_is_passed_through_as_the_format_constraint():
    """Constrained decoding beats asking the model politely in the prompt.

    The schema is forwarded with one addition — `maxItems` on the scenarios
    array — so this asserts the caller's constraints survive rather than
    byte equality. See test_the_schema_sent_to_ollama_bounds_the_scenarios_array.
    """
    schema = {"type": "object", "properties": {"scenarios": {"type": "array"}}}
    payload = shim.build_ollama_payload("AC1: x", response_format=schema)

    sent = payload["format"]
    assert sent["type"] == schema["type"]
    assert sent["properties"]["scenarios"]["type"] == "array"
    assert "maxItems" in sent["properties"]["scenarios"]


def test_without_a_schema_the_format_still_forces_json():
    payload = shim.build_ollama_payload("AC1: x")

    assert payload["format"] == "json"


def test_generation_is_near_greedy():
    """Structured extraction, not creative writing."""
    payload = shim.build_ollama_payload("AC1: x")

    assert payload["options"]["temperature"] <= 0.3
    assert payload["stream"] is False


# ---------------------------------------------------------------------------
# What the shim will and will not return (AC6)
# ---------------------------------------------------------------------------


def test_valid_model_output_becomes_a_bddgenerateresponse_shaped_dict():
    from app.schemas.bdd import BDDGenerateResponse

    result = shim.extract_scenarios(_valid_model_output())

    # The real proof: the backend can parse what the shim would have returned.
    validated = BDDGenerateResponse.model_validate(result)
    assert len(validated.scenarios) == 1
    assert validated.scenarios[0].source_ac_clause == "AC1"
    assert validated.scenarios[0].given.startswith("a registered user")


def test_output_wrapped_in_a_code_fence_still_parses():
    raw = f"```json\n{_valid_model_output()}\n```"

    result = shim.extract_scenarios(raw)
    assert len(result["scenarios"]) == 1


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "I'm sorry, I cannot help with that.", "{not valid json"],
)
def test_unusable_model_output_is_rejected_not_forwarded(raw):
    """A shim that forwards junk just moves the failure into bdd_service."""
    with pytest.raises(ValueError):
        shim.extract_scenarios(raw)


def test_output_missing_required_scenario_fields_is_rejected():
    raw = json.dumps({"scenarios": [{"scenario": "missing everything else"}]})

    with pytest.raises(ValueError, match="BDDGenerateResponse"):
        shim.extract_scenarios(raw)


def test_an_empty_scenarios_array_is_rejected():
    """It VALIDATES (the field defaults to []) and would reach the app empty."""
    with pytest.raises(ValueError, match="zero scenarios"):
        shim.extract_scenarios(json.dumps({"scenarios": []}))


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_generation_is_bounded_by_an_output_token_cap():
    """The timeout bounds the WAIT; this bounds the WORK.

    Without a cap, Ollama generates until the context window runs out. Measured
    on an unnumbered 876-character ticket: a full 300s timeout consumed and
    ZERO scenarios returned. Raising the timeout does not fix that.
    """
    payload = shim.build_ollama_payload("AC1: x")

    assert payload["options"]["num_predict"] == shim.MAX_OUTPUT_TOKENS
    assert shim.MAX_OUTPUT_TOKENS > 0


def test_the_scenario_cap_scales_with_the_ac_clauses():
    """Twice the clause count, so a clause can yield a happy and an error path."""
    assert shim.scenario_cap("AC1: a\nAC2: b\nAC3: c") == 6
    # Clamped below, so a single-clause input can still produce a few.
    assert shim.scenario_cap("AC1: only one") == shim.MIN_SCENARIOS
    # Clamped above, so a huge ticket cannot ask for an unbounded answer.
    many = "\n".join(f"AC{i}: x" for i in range(1, 40))
    assert shim.scenario_cap(many) == shim.MAX_SCENARIOS


def test_unnumbered_criteria_get_a_low_cap():
    """Prose with no clause list is what sends this model into a repetition
    loop — there is no enumeration to work through, so nothing says 'done'."""
    assert shim.scenario_cap("the user can manage their profile") == (
        shim.UNNUMBERED_SCENARIO_CAP
    )


def test_the_schema_sent_to_ollama_bounds_the_scenarios_array():
    """`maxItems` is the bound that actually works.

    Ollama builds a decoding grammar from the schema, so an over-long answer
    becomes unrepresentable rather than truncated. Verified against the model:
    an input that burned a 300s timeout returning nothing finished in 22s with
    valid scenarios once the array was bounded.
    """
    schema = {
        "properties": {"scenarios": {"type": "array", "items": {}}},
        "type": "object",
    }
    payload = shim.build_ollama_payload("AC1: a\nAC2: b", response_format=schema)

    assert payload["format"]["properties"]["scenarios"]["maxItems"] == 4
    # The caller's schema must not be mutated — pydantic may share it.
    assert "maxItems" not in schema["properties"]["scenarios"]


def test_the_token_budget_can_accommodate_the_scenario_cap():
    """A cap the token budget cannot reach never binds.

    At roughly 100 tokens per scenario, a 768-token budget stopped generation
    mid-string long before a 12-item limit applied — which is precisely how the
    first attempt at this fix failed.
    """
    assert shim.MAX_OUTPUT_TOKENS >= shim.MAX_SCENARIOS * 100


def test_the_shim_logger_actually_reaches_a_console():
    """Uvicorn configures only its own loggers.

    Without a handler here every message is discarded — which is what happened
    to the startup chat-template verdict, the one warning that says the served
    model is not the one that was trained. It logged faithfully to nowhere.
    """
    assert shim.logger.handlers, "bdd_shim logger has no handler; output is discarded"
    assert shim.logger.level <= logging.INFO


def test_settings_are_read_from_backend_env_not_only_exports():
    """The shim is configured from backend/.env, the same file as the backend.

    Before this, every start needed four `export` lines and a forgotten one was
    silent: the shim came up on defaults and served the WRONG model.
    """
    env_file = Path(__file__).resolve().parents[1] / ".env"
    assert env_file.exists(), "backend/.env is the shim's configuration source"

    source = (_SERVE_DIR / "app.py").read_text(encoding="utf-8")
    assert "load_dotenv" in source
    # override=False is what keeps a command-line export authoritative.
    assert "override=False" in source


def test_an_exported_variable_still_beats_the_env_file(monkeypatch):
    """A one-off run must be tunable without editing backend/.env."""
    monkeypatch.setenv("SHIM_TIMEOUT_SECONDS", "42")
    monkeypatch.setenv("FINE_TUNED_OLLAMA_MODEL", "some-other-model")

    reloaded = _load_shim()

    assert reloaded.REQUEST_TIMEOUT_SECONDS == 42.0
    assert reloaded.MODEL_NAME == "some-other-model"


def test_shim_timeout_stays_inside_the_providers_budget():
    """The shim must give up BEFORE the provider does.

    FINE_TUNED_MODEL_TIMEOUT_SECONDS bounds the whole exchange, and the
    provider still has to run the general-LLM fallback afterwards inside
    NFR-P3's 30s — so a shim that hangs to the limit is worse than one that
    fails fast.

    Both values are read from backend/.env rather than from `settings`. The
    settings singleton resolves `.env` against the CWD, and there are TWO env
    files in this repo: running the suite from the repo root loads the root
    one, which does not carry these keys, so the comparison silently became
    "the shim's real value versus the backend's default" and failed for a
    reason that had nothing to do with the budget.
    """
    env_file = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()

    provider_budget = float(values.get("FINE_TUNED_MODEL_TIMEOUT_SECONDS", "12"))
    shim_budget = float(values.get("SHIM_TIMEOUT_SECONDS", "10"))

    assert provider_budget > shim_budget, (
        f"SHIM_TIMEOUT_SECONDS ({shim_budget}s) must be under "
        f"FINE_TUNED_MODEL_TIMEOUT_SECONDS ({provider_budget}s), or the "
        "provider gives up first and the shim's own error never surfaces."
    )


async def test_call_model_bounds_the_whole_exchange_not_each_phase(monkeypatch):
    """The budget must be total, not per-phase.

    httpx applies its timeout to connect/read/write/pool SEPARATELY, so handing
    it the value alone permits ~2x on a slow connect — past the provider's 12s.
    The guard is `asyncio.timeout` around the request, mirroring what
    fine_tuned_provider.py does on the other side of the wire. This test asserts
    the bound actually fires, rather than asserting 10 < 12 on a constant.
    """
    import asyncio

    class _HangingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            await asyncio.sleep(60)  # never returns within the budget

    monkeypatch.setattr(shim.httpx, "AsyncClient", lambda **kw: _HangingClient())
    monkeypatch.setattr(shim, "REQUEST_TIMEOUT_SECONDS", 0.05)

    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await shim.call_model({"model": "x", "messages": []})
    assert asyncio.get_running_loop().time() - started < 1.0


# ---------------------------------------------------------------------------
# AC6 — the training chat template is VERIFIED, not assumed
# ---------------------------------------------------------------------------
#
# The original design got this from the Modelfile Unsloth exported beside the
# GGUF. unsloth was dropped mid-story when it broke on Kaggle and the export
# went with it, so nothing carries the template across automatically. These
# tests pin the replacement: compare, and report honestly when we cannot.


def test_matching_templates_verify():
    verdict = shim.compare_templates("{{ messages }}", "{{ messages }}")

    assert verdict["verified"] is True
    assert verdict["status"] == "match"


def test_whitespace_only_difference_still_verifies():
    verdict = shim.compare_templates("{{ messages }}\n", "  {{ messages }}  ")

    assert verdict["verified"] is True


def test_a_different_served_template_is_reported_as_a_mismatch():
    """The silent-degradation failure AC6 exists to prevent.

    Fixtures must carry real control tokens: this test originally compared two
    token-less strings, which is now correctly `unknown` rather than
    `mismatch`. A mismatch means "we read both and they disagree".
    """
    verdict = shim.compare_templates("<|im_start|>x<|im_end|>", "<|eot_id|>x")

    assert verdict["verified"] is False
    assert verdict["status"] == "mismatch"
    assert "silently" in verdict["detail"]


@pytest.mark.parametrize(
    ("trained", "served"),
    [(None, "{{ x }}"), ("{{ x }}", None), (None, None)],
)
def test_a_missing_side_is_unknown_not_a_pass_and_not_a_mismatch(trained, served):
    """'We could not check' and 'we checked' are different facts.

    Collapsing them is how the Unsloth-Modelfile claim outlived the export.
    """
    verdict = shim.compare_templates(trained, served)

    assert verdict["verified"] is False
    assert verdict["status"] == "unknown"


# --- Jinja vs Go: the case that broke AC4 in Story 6.2 ---------------------
#
# The adapter ships a JINJA template (transformers wrote it). Ollama's
# /api/show returns a GO template. They are different LANGUAGES, so comparing
# source text reports "mismatch" for a model whose wire format is identical —
# a false alarm on exactly the case the check exists to bless. What actually
# determines whether serving matches training is the special-token skeleton.

_QWEN_JINJA = (
    "{%- if messages[0]['role'] == 'system' %}"
    "{{- '<|im_start|>system\\n' + messages[0]['content'] + '<|im_end|>\\n' }}"
    "{%- endif %}"
    "{{- '<|im_start|>assistant\\n' }}"
)
_QWEN_GO = (
    "{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{- end }}"
    "<|im_start|>assistant\n"
)
_LLAMA3_GO = (
    "<|start_header_id|>system<|end_header_id|>\n{{ .System }}<|eot_id|>"
    "<|start_header_id|>assistant<|end_header_id|>"
)


def test_equivalent_templates_in_different_languages_verify_by_wire_format():
    """Jinja and Go source differ; the ChatML tokens they emit do not."""
    verdict = shim.compare_templates(_QWEN_JINJA, _QWEN_GO)

    assert verdict["verified"] is True
    assert verdict["status"] == "match"
    assert verdict["comparison"] == "wire-format"


def test_byte_identical_templates_are_reported_as_an_exact_comparison():
    """Distinguished from wire-format so /health never overclaims."""
    verdict = shim.compare_templates(_QWEN_GO, _QWEN_GO)

    assert verdict["verified"] is True
    assert verdict["comparison"] == "exact"


def test_a_different_token_family_is_still_a_mismatch():
    """The failure that matters: a Llama-3 base under a Qwen fine-tune."""
    verdict = shim.compare_templates(_QWEN_JINJA, _LLAMA3_GO)

    assert verdict["verified"] is False
    assert verdict["status"] == "mismatch"


def test_templates_with_no_special_tokens_report_unknown_not_mismatch():
    """Two token-less templates prove nothing — but that is NOT a mismatch.

    Regression for a review finding: this test previously asserted only
    `verified is False`, so it passed while the verdict was the wrong kind of
    failure. "We could not check" and "we checked and they differ" are the
    distinction this module exists to preserve.
    """
    verdict = shim.compare_templates("{{ a }}", "{{ b }}")

    assert verdict["verified"] is False
    assert verdict["status"] == "unknown"


def test_a_token_family_the_checker_cannot_read_is_unknown_not_mismatch():
    """Gemma-style templates use <start_of_turn>, not <|...|>.

    A correctly-served Gemma model must not be reported as a mismatch just
    because this checker only understands pipe-delimited control tokens.
    """
    gemma_jinja = (
        "{% for m in messages %}<start_of_turn>{{ m.role }}<end_of_turn>{% endfor %}"
    )
    gemma_go = "{{ range .Messages }}<start_of_turn>{{ .Role }}<end_of_turn>{{ end }}"

    verdict = shim.compare_templates(gemma_jinja, gemma_go)

    assert verdict["status"] == "unknown"
    assert "could not" in verdict["detail"].lower()


def test_hyphenated_and_dotted_control_tokens_are_recognised():
    """Token names are not restricted to [a-zA-Z0-9_] across model families."""
    tokens = shim.template_wire_format("<|end-of-turn|>")
    assert tokens == frozenset({"<|end-of-turn|>"})


def test_the_training_template_is_read_from_the_adapter_dir(tmp_path, monkeypatch):
    template_file = tmp_path / shim.CHAT_TEMPLATE_FILENAME
    template_file.write_text("{{ trained }}", encoding="utf-8")
    monkeypatch.setattr(shim, "ADAPTER_DIR", tmp_path)

    assert shim.load_training_template() == "{{ trained }}"


def test_a_missing_adapter_dir_yields_none_rather_than_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shim, "ADAPTER_DIR", tmp_path / "does-not-exist")

    assert shim.load_training_template() is None


async def test_an_unreachable_ollama_makes_the_verdict_unknown_not_a_crash(monkeypatch):
    """/health must answer even when the model backend is down."""

    async def _boom():
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(shim, "fetch_served_template", _boom)
    monkeypatch.setattr(shim, "load_training_template", lambda: "{{ trained }}")

    verdict = await shim.verify_chat_template()

    assert verdict["status"] == "unknown"


async def test_health_reports_the_template_verdict(monkeypatch):
    """The verdict has to be reachable without reading the startup log."""
    monkeypatch.setattr(shim, "_template_verdict", None)
    monkeypatch.setattr(shim, "load_training_template", lambda: "<|im_start|>")

    async def _served():
        return "<|im_start|>"

    monkeypatch.setattr(shim, "fetch_served_template", _served)

    body = await shim.health()

    assert body["status"] == "ok"
    assert body["chat_template"]["verified"] is True
    assert "adapter_dir" in body


async def test_health_serves_a_cached_verdict_rather_than_calling_ollama(monkeypatch):
    """/health must not make an outbound call on every request.

    It is polled by monitoring and would otherwise be slowest exactly when the
    model is saturated — which on CPU means 45s at a time. The live check runs
    at startup and on explicit ?refresh=true.
    """
    calls = 0

    async def _served():
        nonlocal calls
        calls += 1
        return "<|im_start|>"

    monkeypatch.setattr(shim, "_template_verdict", None)
    monkeypatch.setattr(shim, "load_training_template", lambda: "<|im_start|>")
    monkeypatch.setattr(shim, "fetch_served_template", _served)

    await shim.health()  # cold: computes once
    await shim.health()
    await shim.health()
    assert calls == 1, "cached verdict should be reused"

    await shim.health(refresh=True)
    assert calls == 2, "?refresh=true must re-check"


async def test_a_model_backend_timeout_is_mapped_to_502(monkeypatch):
    """The provider treats non-2xx as a fallback trigger; an unhandled 500 with
    a stack trace is not the same contract."""

    async def _timeout(_payload):
        raise TimeoutError()

    monkeypatch.setattr(shim, "call_model", _timeout)

    response = await shim.generate(
        shim.GenerateRequest(acceptance_criteria="AC1: x")
    )

    assert response.status_code == 502
    assert b"MODEL_BACKEND_ERROR" in response.body
