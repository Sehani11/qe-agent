"""Per-request LLM selection: shared schema, resolution, and rejection."""

import pytest
from fastapi import HTTPException

from app.api.v1.llm_selection import (
    llm_for,
    llm_with_tools_for,
    validate_bdd_selection,
    validate_selection,
)
from app.schemas.bdd import BDDGenerateRequest
from app.schemas.chat import ChatMessageRequest, KnowledgeChatRequest
from app.schemas.llm import LLMSelectionMixin
from app.schemas.verification import AgenticVerificationRequest
from app.services.bdd_model.factory import get_bdd_model_provider
from app.services.llm.openai_provider import OpenAIProvider

_SELECTION_FIELDS = ("llm_provider", "llm_model")


@pytest.mark.parametrize(
    "model",
    [ChatMessageRequest, KnowledgeChatRequest, AgenticVerificationRequest],
)
def test_llm_requests_carry_the_selection(model: type) -> None:
    """Every LLM-backed request accepts a per-request provider/model."""
    for field in _SELECTION_FIELDS:
        assert field in model.model_fields
        assert model.model_fields[field].default is None


def test_bdd_request_matches_the_shared_llm_selection() -> None:
    """BDDGenerateRequest declares the mixin's fields inline — keep them identical.

    `app/schemas/bdd.py` is loaded standalone by `training/serve/app.py`, so it
    cannot import the mixin. This is the guard that stops the copy from
    drifting: same names, same types, same defaults, same descriptions.
    """
    shared = LLMSelectionMixin.model_fields
    inline = BDDGenerateRequest.model_fields

    for field in _SELECTION_FIELDS:
        assert field in inline, f"BDDGenerateRequest lost {field}"
        assert inline[field].annotation == shared[field].annotation
        assert inline[field].default == shared[field].default
        assert inline[field].description == shared[field].description


def test_selection_resolves_to_the_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider handed to a service is the one the request named."""
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "sk-test")

    request = KnowledgeChatRequest(
        question="q", llm_provider="openai", llm_model="gpt-4o-mini"
    )
    provider = llm_for(request)

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o-mini"


def test_unknown_provider_is_a_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsupported name is 400, not the 500 an unhandled provider error gives."""
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "sk-test")

    request = KnowledgeChatRequest(question="q", llm_provider="gemini")

    with pytest.raises(HTTPException) as excinfo:
        llm_for(request)
    assert excinfo.value.status_code == 400

    with pytest.raises(HTTPException) as excinfo:
        validate_selection(request)
    assert excinfo.value.status_code == 400


def test_validate_selection_allows_an_unset_choice() -> None:
    """No choice means the server default, which is not an error."""
    validate_selection(KnowledgeChatRequest(question="q"))
    validate_selection(KnowledgeChatRequest(question="q", llm_provider=""))


# ---------------------------------------------------------------------------
# Credentials and capabilities are per provider, not per deployment
# ---------------------------------------------------------------------------


def test_a_providers_key_is_never_used_for_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting Claude on an OpenAI-only deployment is a clear 400.

    Each vendor reads only its own variable, so an OpenAI key cannot stand in
    for Anthropic. Letting it would satisfy that provider's "is a key
    configured" check and then fail at call time as a 401 — which reads as a
    broken account rather than as a missing setting.
    """
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.openai_api_key", "sk-openai"
    )
    monkeypatch.setattr("app.services.llm.factory.settings.anthropic_api_key", "")

    with pytest.raises(HTTPException) as excinfo:
        llm_for(KnowledgeChatRequest(question="q", llm_provider="claude"))

    assert excinfo.value.status_code == 400
    assert "ANTHROPIC_API_KEY" in excinfo.value.detail


def test_a_provider_is_built_from_its_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_PROVIDER selects the DEFAULT provider; it no longer selects a key.

    It used to do both, through the shared LLM_API_KEY, which is what let one
    vendor's credential reach another.
    """
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "claude")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.openai_api_key", "sk-openai"
    )

    provider = llm_for(KnowledgeChatRequest(question="q", llm_provider="openai"))
    assert isinstance(provider, OpenAIProvider)


def test_verification_refuses_a_provider_that_cannot_call_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama has no tool loop, so the run is refused instead of faked.

    Without this the service catches the per-scenario failure, emits an error
    event, continues, and still reports a completed run — a verification that
    verified nothing.
    """
    monkeypatch.setattr(
        "app.services.llm.factory.settings.ollama_base_url", "http://localhost:11434"
    )
    monkeypatch.setattr("app.services.llm.factory.settings.ollama_model", "llama3.2:3b")

    with pytest.raises(HTTPException) as excinfo:
        llm_with_tools_for(KnowledgeChatRequest(question="q", llm_provider="local"))

    assert excinfo.value.status_code == 400
    assert "does not support the tool calling" in excinfo.value.detail


def test_verification_accepts_the_providers_that_can(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both hosted providers implement the loop, so neither is blocked."""
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "sk-openai")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.anthropic_api_key", "sk-anthropic"
    )

    for name in ("openai", "claude"):
        provider = llm_with_tools_for(
            KnowledgeChatRequest(question="q", llm_provider=name)
        )
        assert provider.supports_tools


# ---------------------------------------------------------------------------
# BDD generation: a second axis, chosen per request
# ---------------------------------------------------------------------------


def test_bdd_request_carries_the_generation_provider() -> None:
    """The session page picks fine-tuned vs general per generation."""
    assert "bdd_model_provider" in BDDGenerateRequest.model_fields
    assert BDDGenerateRequest.model_fields["bdd_model_provider"].default is None


@pytest.mark.parametrize("name", ["general_llm", "fine_tuned"])
def test_supported_bdd_providers_are_accepted(name: str) -> None:
    validate_bdd_selection(name)


def test_unset_bdd_provider_means_the_server_default() -> None:
    validate_bdd_selection(None)
    validate_bdd_selection("")


def test_unknown_bdd_provider_is_a_client_error() -> None:
    """Resolved several calls deep, so without this it surfaces as a 500."""
    with pytest.raises(HTTPException) as excinfo:
        validate_bdd_selection("some_old_provider")

    assert excinfo.value.status_code == 400
    assert "Unsupported BDD model provider" in excinfo.value.detail


def test_requested_bdd_provider_overrides_the_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit choice wins over BDD_MODEL_PROVIDER, without touching it.

    The factory used to be steered by swapping the global setting; a request
    that changed it for the whole process would have made concurrent
    generations answer from the wrong model.
    """
    monkeypatch.setattr(
        "app.services.bdd_model.factory.settings.bdd_model_provider", "general_llm"
    )

    provider = get_bdd_model_provider(provider="fine_tuned")

    assert provider.name == "fine_tuned"
    # The setting itself is untouched — nothing global moved.
    from app.core.config import settings

    assert settings.bdd_model_provider == "general_llm"
