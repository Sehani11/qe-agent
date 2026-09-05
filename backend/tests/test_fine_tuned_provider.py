"""Tests for FineTunedModelProvider request and fallback branches.

The factory-level selection tests live in test_bdd_model_factory.py. This
module covers what happens once FineTunedModelProvider.generate_bdd is
actually called: the HTTP request it builds, and every path that silently
degrades to the general LLM.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.bdd_model.fine_tuned_provider import FineTunedModelProvider
from app.services.bdd_model.general_llm_fallback import GeneralLLMFallbackProvider
from app.services.bdd_model.provider import BDDModelProviderError

_MOD = "app.services.bdd_model.fine_tuned_provider"
_ENDPOINT = "https://fine-tuned.example.com/generate"
_AC = "AC1: A registered user can log in with valid credentials."

_FINE_TUNED_RESULT: dict[str, object] = {
    "scenarios": [
        {
            "source_ac_clause": "AC1",
            "feature": "Login",
            "scenario": "Valid login",
            "given": "Given a registered user",
            "when": "When they submit valid credentials",
            "then": "Then the dashboard loads",
        }
    ]
}
_FALLBACK_RESULT: dict[str, object] = {"scenarios": []}


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str = _ENDPOINT,
    api_key: str = "",
) -> None:
    """Patch provider settings BEFORE construction — __init__ reads them."""
    monkeypatch.setattr(f"{_MOD}.settings.fine_tuned_model_endpoint", endpoint)
    monkeypatch.setattr(f"{_MOD}.settings.fine_tuned_model_api_key", api_key)


def _mock_response(
    json_data: object = None,
    raise_for_status_exc: Exception | None = None,
    json_exc: Exception | None = None,
) -> MagicMock:
    """Build a mock httpx.Response."""
    mock = MagicMock()
    if raise_for_status_exc is not None:
        mock.raise_for_status.side_effect = raise_for_status_exc
    else:
        mock.raise_for_status.return_value = None
    if json_exc is not None:
        mock.json.side_effect = json_exc
    else:
        mock.json.return_value = json_data
    return mock


def _make_async_client(post: AsyncMock) -> MagicMock:
    """Build an async context-manager mock for httpx.AsyncClient."""
    mock_client = AsyncMock()
    mock_client.post = post
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", _ENDPOINT)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}",
        request=request,
        response=httpx.Response(status_code, request=request),
    )


# ---------------------------------------------------------------------------
# Endpoint success path
# ---------------------------------------------------------------------------


async def test_endpoint_success_returns_parsed_json_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy endpoint's JSON is returned verbatim, with no fallback."""
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FINE_TUNED_RESULT
    mock_fallback.assert_not_awaited()

    post.assert_awaited_once()
    assert post.await_args.args[0] == _ENDPOINT
    assert post.await_args.kwargs["json"]["acceptance_criteria"] == _AC


async def test_system_prompt_and_response_format_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optional generation arguments reach the endpoint payload when supplied."""
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))
    schema: dict[str, object] = {"type": "object"}

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        await FineTunedModelProvider().generate_bdd(
            _AC, system_prompt="Be terse.", response_format=schema
        )

    payload = post.await_args.kwargs["json"]
    assert payload["system_prompt"] == "Be terse."
    assert payload["response_format"] == schema


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------


async def test_bearer_header_sent_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINE_TUNED_MODEL_API_KEY is sent as a Bearer token when configured."""
    _configure(monkeypatch, api_key="secret-key")
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        await FineTunedModelProvider().generate_bdd(_AC)

    assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer secret-key"


async def test_no_auth_header_when_api_key_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Authorization header is sent when no API key is configured."""
    _configure(monkeypatch, api_key="")
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        await FineTunedModelProvider().generate_bdd(_AC)

    assert "Authorization" not in post.await_args.kwargs["headers"]


# ---------------------------------------------------------------------------
# Fallback paths — endpoint reachable but failing
# ---------------------------------------------------------------------------


async def test_http_error_falls_back_to_general_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-2xx response degrades to the general LLM rather than raising."""
    _configure(monkeypatch)
    post = AsyncMock(
        return_value=_mock_response(raise_for_status_exc=_http_status_error(500))
    )

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()


async def test_transport_error_falls_back_to_general_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout or connection failure degrades to the general LLM."""
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()


async def test_non_json_body_falls_back_to_general_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 response with an unparseable body degrades to the general LLM."""
    _configure(monkeypatch)
    post = AsyncMock(
        return_value=_mock_response(
            json_exc=json.JSONDecodeError("Expecting value", "", 0)
        )
    )

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Fallback paths — endpoint not configured / fallback itself fails
# ---------------------------------------------------------------------------


async def test_unset_endpoint_falls_back_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no endpoint configured, no HTTP request is attempted at all."""
    _configure(monkeypatch, endpoint="")

    with (
        patch(f"{_MOD}.httpx.AsyncClient") as mock_client_class,
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()
    mock_client_class.assert_not_called()


async def test_fallback_failure_raises_bdd_model_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the endpoint AND the general LLM fail, the error surfaces."""
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.side_effect = BDDModelProviderError("LLM unreachable")

        with pytest.raises(BDDModelProviderError, match="Both fine-tuned model"):
            await FineTunedModelProvider().generate_bdd(_AC)


# ---------------------------------------------------------------------------
# Effective-provider observability
#
# A fallback response is byte-identical to a real fine-tuned response, so
# without these log lines Story 6.3's evaluation pipeline cannot tell whether
# it measured the fine-tuned model or the general LLM wearing its label.
# ---------------------------------------------------------------------------


def test_provider_names_match_factory_keys() -> None:
    """Each provider's name equals the BDD_MODEL_PROVIDER value that selects it."""
    from app.services.bdd_model.factory import _PROVIDERS

    for env_value, provider_class in _PROVIDERS.items():
        assert provider_class.name == env_value


async def test_endpoint_error_fallback_records_effective_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back after an endpoint failure records the real provider used."""
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))
    provider = FineTunedModelProvider()

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        await provider.generate_bdd(_AC)

    assert provider.effective_provider == "general_llm"
    assert provider.fallback_reason == "endpoint_error"


async def test_unset_endpoint_fallback_records_distinct_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back because no endpoint is configured is distinguishable."""
    _configure(monkeypatch, endpoint="")
    provider = FineTunedModelProvider()

    with patch.object(
        GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
    ) as mock_fallback:
        mock_fallback.return_value = _FALLBACK_RESULT
        await provider.generate_bdd(_AC)

    assert provider.effective_provider == "general_llm"
    assert provider.fallback_reason == "endpoint_unset"


async def test_endpoint_success_records_fine_tuned_as_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine fine-tuned response is attributed to the fine-tuned model."""
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))
    provider = FineTunedModelProvider()

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        await provider.generate_bdd(_AC)

    assert provider.effective_provider == "fine_tuned"
    assert provider.fallback_reason is None


async def test_providers_emit_no_attribution_logs_of_their_own(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Attribution is logged once by bdd_service — never duplicated per provider.

    Providers previously logged their own effective-provider line, which meant a
    single fallback generation emitted two, double-counting for any log-scraping
    consumer.
    """
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
        caplog.at_level(logging.INFO),
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        await FineTunedModelProvider().generate_bdd(_AC)

    assert "effective_provider=" not in caplog.text
    assert "bdd_model.generation" not in caplog.text


# ---------------------------------------------------------------------------
# Credential safety
# ---------------------------------------------------------------------------


async def test_endpoint_credentials_are_not_written_to_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A query-string API key must never reach the logs on failure.

    httpx's raise_for_status() embeds the full URL in its message, so logging
    str(e) would leak the key.
    """
    secret_url = "https://ft.example.com/gen?api-key=SUPERSECRET123"
    _configure(monkeypatch, endpoint=secret_url)

    request = httpx.Request("POST", secret_url)
    response = httpx.Response(500, request=request)
    real_error: Exception | None = None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:  # capture httpx's real message
        real_error = exc

    post = AsyncMock(return_value=_mock_response(raise_for_status_exc=real_error))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
        caplog.at_level(logging.WARNING),
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        await FineTunedModelProvider().generate_bdd(_AC)

    assert "SUPERSECRET123" not in caplog.text
    assert "api-key" not in caplog.text
    # The redacted host/path still appears, so the log stays actionable
    assert "https://ft.example.com/gen" in caplog.text
    assert "HTTP 500" in caplog.text


# ---------------------------------------------------------------------------
# Additional decode failures
# ---------------------------------------------------------------------------


async def test_undecodable_body_falls_back_to_general_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body with invalid encoding degrades rather than escaping the provider."""
    _configure(monkeypatch)
    decode_error = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")
    post = AsyncMock(return_value=_mock_response(json_exc=decode_error))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Timeout budget (NFR-P3)
#
# On a hung endpoint the request must not consume the whole 30s generation
# budget, because the general-LLM fallback still has to run afterwards.
# ---------------------------------------------------------------------------


def test_default_timeout_leaves_room_for_fallback_within_nfr_p3() -> None:
    """The declared default must leave time for a fallback LLM call under 30s."""
    from app.core.config import Settings

    default = Settings.model_fields["fine_tuned_model_timeout_seconds"].default
    assert 0 < default <= 12.0


async def test_configured_timeout_is_passed_to_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINE_TUNED_MODEL_TIMEOUT_SECONDS reaches the httpx client."""
    _configure(monkeypatch)
    monkeypatch.setattr(f"{_MOD}.settings.fine_tuned_model_timeout_seconds", 7.5)
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))

    with patch(
        f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)
    ) as mock_client_class:
        await FineTunedModelProvider().generate_bdd(_AC)

    assert mock_client_class.call_args.kwargs["timeout"] == 7.5


async def test_slow_endpoint_is_bounded_by_total_wall_clock_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung endpoint must abort at the budget, not stall per-phase.

    httpx's own timeout applies the full value to EACH phase (connect, read,
    write, pool), so it cannot guarantee a total bound. Only the asyncio
    timeout keeps the request inside the slice of NFR-P3 reserved for it.
    """
    _configure(monkeypatch)
    monkeypatch.setattr(f"{_MOD}.settings.fine_tuned_model_timeout_seconds", 0.05)

    async def never_returns(*args: object, **kwargs: object) -> object:
        await asyncio.sleep(10)
        raise AssertionError("request should have been cancelled by the budget")

    post = AsyncMock(side_effect=never_returns)

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        started = asyncio.get_running_loop().time()
        result = await FineTunedModelProvider().generate_bdd(_AC)
        elapsed = asyncio.get_running_loop().time() - started

    assert result == _FALLBACK_RESULT
    assert elapsed < 2.0, f"budget not enforced; waited {elapsed:.2f}s"
    mock_fallback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Story 6.2 AC5 — the endpoint body is validated before it is returned
# ---------------------------------------------------------------------------
#
# Story 6.1 shipped `return response.json()` unvalidated and deferred the fix
# to 6.2, "when a real endpoint exists to validate against". It now does. A
# wrong-shaped body previously escaped the provider and surfaced through
# bdd_service's bare `except Exception` as "Failed to parse or validate model
# output" — a parse error blamed on the model, with no fallback and no
# attribution. It is a degradation like any other, and belongs in the same
# fallback path as an HTTP error.


async def test_conforming_body_is_returned_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation must not rewrite a good response."""
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data=_FINE_TUNED_RESULT))

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        provider = FineTunedModelProvider()
        result = await provider.generate_bdd(_AC)

    assert result == _FINE_TUNED_RESULT
    assert provider.effective_provider == "fine_tuned"
    assert provider.fallback_reason is None


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"scenarios": [{"scenario": "missing every other field"}]}, "incomplete"),
        ({"wrong_key": []}, "no scenarios key"),
        ({"scenarios": "not-a-list"}, "wrong type"),
        ({"scenarios": []}, "empty array"),
    ],
    ids=["incomplete-scenario", "missing-key", "wrong-type", "empty-array"],
)
async def test_nonconforming_body_falls_back_instead_of_reaching_bdd_service(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, object], why: str
) -> None:
    """Valid JSON that BDDGenerateResponse cannot use is a degradation.

    The empty-array case matters most: it VALIDATES (the field defaults to [])
    and would otherwise reach the user as a successful generation containing
    no scenarios at all.
    """
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data=body))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        provider = FineTunedModelProvider()
        result = await provider.generate_bdd(_AC)

    assert result == _FALLBACK_RESULT, f"{why} should have degraded"
    mock_fallback.assert_awaited_once()


async def test_invalid_shape_is_attributed_separately_from_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 6.3 must tell 'endpoint down' apart from 'endpoint misbehaving'.

    Both degrade, but they call for completely different fixes, and the
    attribution line is the only place that distinction survives.
    """
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data={"wrong_key": []}))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        provider = FineTunedModelProvider()
        await provider.generate_bdd(_AC)

    assert provider.effective_provider == "general_llm"
    assert provider.fallback_reason == "endpoint_invalid_response"


# ---------------------------------------------------------------------------
# allow_fallback=False — for callers that MEASURE this model
# ---------------------------------------------------------------------------


async def test_fallback_disabled_raises_instead_of_calling_the_general_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A comparison must not have the general LLM answer under this label.

    Falling back there fills the fine-tuned column with the other model's
    output, so two columns describe one model with nothing to distinguish that
    from a real comparison. The general LLM must not even be CALLED — the
    result would only be discarded, at full cost.
    """
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        provider = FineTunedModelProvider(allow_fallback=False)
        with pytest.raises(BDDModelProviderError, match="fallback is disabled"):
            await provider.generate_bdd(_AC)

    mock_fallback.assert_not_awaited()


async def test_fallback_disabled_still_records_why_it_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller must still be able to report the CAUSE, not just a failure."""
    _configure(monkeypatch)
    post = AsyncMock(return_value=_mock_response(json_data={"wrong_key": []}))

    with patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)):
        provider = FineTunedModelProvider(allow_fallback=False)
        with pytest.raises(BDDModelProviderError):
            await provider.generate_bdd(_AC)

    assert provider.fallback_reason == "endpoint_invalid_response"
    # It never became the general LLM, so this must not claim otherwise.
    assert provider.effective_provider != "general_llm"


async def test_unset_endpoint_with_fallback_disabled_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, endpoint="")

    with patch.object(
        GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
    ) as mock_fallback:
        provider = FineTunedModelProvider(allow_fallback=False)
        with pytest.raises(BDDModelProviderError, match="endpoint_unset"):
            await provider.generate_bdd(_AC)

    mock_fallback.assert_not_awaited()


async def test_serving_still_falls_back_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production default must be unchanged: users get scenarios, not errors."""
    _configure(monkeypatch)
    post = AsyncMock(side_effect=httpx.ConnectTimeout("endpoint timed out"))

    with (
        patch(f"{_MOD}.httpx.AsyncClient", return_value=_make_async_client(post)),
        patch.object(
            GeneralLLMFallbackProvider, "generate_bdd", new_callable=AsyncMock
        ) as mock_fallback,
    ):
        mock_fallback.return_value = _FALLBACK_RESULT
        result = await FineTunedModelProvider().generate_bdd(_AC)

    assert result == _FALLBACK_RESULT
    mock_fallback.assert_awaited_once()
