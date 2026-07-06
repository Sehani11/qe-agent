"""Tests for the BDD model provider factory and interface."""

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
    import logging
    with caplog.at_level(logging.WARNING):
        provider = FineTunedModelProvider()
    assert "FINE_TUNED_MODEL_ENDPOINT is not configured" in caplog.text
