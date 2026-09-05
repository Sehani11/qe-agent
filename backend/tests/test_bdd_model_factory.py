"""Tests for the BDD model provider factory and interface."""

import logging
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.bdd_model.factory import get_bdd_model_provider
from app.services.bdd_model.fine_tuned_provider import FineTunedModelProvider
from app.services.bdd_model.general_llm_fallback import GeneralLLMFallbackProvider
from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError


def test_bdd_model_provider_abc_cannot_be_instantiated() -> None:
    """BDDModelProvider is abstract and should not be instantiable."""
    with pytest.raises(TypeError):
        BDDModelProvider()  # type: ignore[abstract]


def test_factory_returns_general_llm_fallback_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory should return GeneralLLMFallbackProvider when BDD_MODEL_PROVIDER=general_llm."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "general_llm"
    )
    provider = get_bdd_model_provider()
    assert isinstance(provider, GeneralLLMFallbackProvider)


def test_factory_returns_fine_tuned_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory should return FineTunedModelProvider when BDD_MODEL_PROVIDER=fine_tuned."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "fine_tuned"
    )
    provider = get_bdd_model_provider()
    assert isinstance(provider, FineTunedModelProvider)


def test_factory_raises_for_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory should raise BDDModelProviderError for unknown providers."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "random_model"
    )
    with pytest.raises(BDDModelProviderError, match="Unsupported BDD model provider"):
        get_bdd_model_provider()


def test_factory_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory should handle case-insensitive provider names."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "General_LLM"
    )
    provider = get_bdd_model_provider()
    assert isinstance(provider, GeneralLLMFallbackProvider)


def test_fine_tuned_provider_warns_when_no_endpoint(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """FineTunedModelProvider should log a warning if endpoint is not configured."""
    monkeypatch.setattr(
        "app.services.bdd_model.fine_tuned_provider.settings.fine_tuned_model_endpoint", ""
    )
    monkeypatch.setattr(
        "app.services.bdd_model.fine_tuned_provider.settings.fine_tuned_model_api_key", ""
    )
    with caplog.at_level(logging.WARNING):
        provider = FineTunedModelProvider()
    assert "FINE_TUNED_MODEL_ENDPOINT is not configured" in caplog.text


# ---------------------------------------------------------------------------
# Effective-provider observability
# ---------------------------------------------------------------------------


async def test_general_llm_provider_records_itself_as_effective() -> None:
    """The general LLM path records that it served the request."""
    mock_llm = AsyncMock()
    mock_llm.generate_structured.return_value = {"scenarios": []}

    provider = GeneralLLMFallbackProvider()
    with patch(
        "app.services.bdd_model.general_llm_fallback.get_llm_provider",
        return_value=mock_llm,
    ):
        await provider.generate_bdd("AC1: something")

    assert provider.effective_provider == "general_llm"
    assert provider.fallback_reason is None


def test_factory_returns_a_fresh_instance_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attribution state is per-instance, so instances must never be shared.

    bdd_service reads provider.effective_provider after the call; if the factory
    cached one instance, concurrent generations would overwrite each other's
    attribution and Story 6.3 would mis-attribute output.
    """
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "general_llm"
    )
    assert get_bdd_model_provider() is not get_bdd_model_provider()


def _attribution_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r for r in caplog.text.splitlines() if "bdd_model.generation" in r]


async def test_bdd_service_logs_one_correlated_attribution_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exactly one attribution line per generation, carrying the session join key."""
    from app.services.bdd_service import generate_bdd_scenarios

    session_id = uuid.uuid4()
    mock_provider = AsyncMock()
    mock_provider.name = "fine_tuned"
    mock_provider.effective_provider = "general_llm"
    mock_provider.fallback_reason = "endpoint_error"
    mock_provider.generate_bdd.return_value = {"scenarios": []}

    with (
        patch(
            "app.services.bdd_service.get_bdd_model_provider",
            return_value=mock_provider,
        ),
        caplog.at_level(logging.INFO),
    ):
        await generate_bdd_scenarios(session_id, "AC1: something")

    lines = _attribution_lines(caplog)
    assert len(lines) == 1
    assert f"session_id={session_id}" in lines[0]
    assert "configured=fine_tuned" in lines[0]
    assert "effective=general_llm" in lines[0]
    assert "reason=endpoint_error" in lines[0]


async def test_bdd_service_logs_attribution_even_when_generation_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed generation is still attributed — 'every generation' means every."""
    from app.services.bdd_service import BDDServiceError, generate_bdd_scenarios

    session_id = uuid.uuid4()
    mock_provider = AsyncMock()
    mock_provider.name = "general_llm"
    mock_provider.effective_provider = None
    mock_provider.fallback_reason = None
    mock_provider.generate_bdd.side_effect = BDDModelProviderError("LLM down")

    with (
        patch(
            "app.services.bdd_service.get_bdd_model_provider",
            return_value=mock_provider,
        ),
        caplog.at_level(logging.INFO),
        pytest.raises(BDDServiceError),
    ):
        await generate_bdd_scenarios(session_id, "AC1: something")

    lines = _attribution_lines(caplog)
    assert len(lines) == 1
    assert f"session_id={session_id}" in lines[0]
    assert "configured=general_llm" in lines[0]
    assert "effective=none" in lines[0]


# ---------------------------------------------------------------------------
# FINE_TUNED_ALLOW_FALLBACK — making `fine_tuned` actually mean fine-tuned
#
# The serving path defaulted to falling back so that a user got scenarios
# rather than an error. That is the right default for an end user and the wrong
# one for a demo or a screenshot: the response is general-LLM output wearing the
# fine_tuned label, and nothing downstream can tell.
# ---------------------------------------------------------------------------


def test_the_setting_supplies_the_serving_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that names no preference takes it from configuration."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "fine_tuned"
    )
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.fine_tuned_allow_fallback", False
    )

    provider = get_bdd_model_provider()

    assert provider._allow_fallback is False


def test_the_setting_still_permits_falling_back_when_left_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "fine_tuned"
    )
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.fine_tuned_allow_fallback", True
    )

    assert get_bdd_model_provider()._allow_fallback is True


def test_a_measuring_caller_cannot_be_overridden_by_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE POINT OF THE EXPLICIT ARGUMENT. The evaluation runner passes False so
    that a fine-tuned failure is a failed row rather than a general-LLM row
    wearing the fine_tuned label. Letting configuration switch that back on
    would silently corrupt the comparison the runner exists to produce."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "fine_tuned"
    )
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.fine_tuned_allow_fallback", True
    )

    assert get_bdd_model_provider(allow_fallback=False)._allow_fallback is False


def test_the_setting_does_not_disturb_the_general_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The general provider takes no such argument; forwarding one would turn a
    serving call into a TypeError."""
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "general_llm"
    )
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.fine_tuned_allow_fallback", False
    )

    assert isinstance(get_bdd_model_provider(), GeneralLLMFallbackProvider)
