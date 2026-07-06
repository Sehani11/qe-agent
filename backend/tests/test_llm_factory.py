"""Tests for the LLM provider factory and interface."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.factory import get_llm_provider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.provider import LLMProvider, LLMProviderError


def test_llm_provider_abc_cannot_be_instantiated() -> None:
    """LLMProvider is abstract and should not be instantiable."""
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


def test_factory_returns_claude_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should return ClaudeProvider when LLM_PROVIDER=claude."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "claude")
    monkeypatch.setattr("app.services.llm.factory.settings.llm_api_key", "test-key")
    provider = get_llm_provider()
    assert isinstance(provider, ClaudeProvider)


def test_factory_returns_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should return OpenAIProvider when LLM_PROVIDER=openai."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    monkeypatch.setattr("app.services.llm.factory.settings.llm_api_key", "test-key")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)


def test_factory_returns_local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should return OllamaProvider when LLM_PROVIDER=local."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "local")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.ollama_base_url",
        "http://localhost:11434",
    )
    monkeypatch.setattr(
        "app.services.llm.factory.settings.ollama_model",
        "llama3.2:3b",
    )
    provider = get_llm_provider()
    assert isinstance(provider, OllamaProvider)


def test_factory_raises_for_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory should raise LLMProviderError for unknown providers."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "gemini")
    with pytest.raises(LLMProviderError, match="Unsupported LLM provider"):
        get_llm_provider()


@pytest.mark.asyncio
async def test_claude_provider_handles_api_error() -> None:
    """Claude provider should wrap SDK errors in LLMProviderError."""
    provider = ClaudeProvider(api_key="test")

    mock_target = provider._client.messages
    with patch.object(mock_target, "create", new_callable=AsyncMock) as m_create:
        from anthropic import APIError
        from httpx import Request
        m_create.side_effect = APIError(
            message="auth error", request=Request("POST", ""), body={}
        )
        with pytest.raises(LLMProviderError, match="Claude API error"):
            await provider.generate("test prompt")


@pytest.mark.asyncio
async def test_openai_provider_handles_api_error() -> None:
    """OpenAI provider should wrap SDK errors in LLMProviderError."""
    provider = OpenAIProvider(api_key="test")

    mock_target = provider._client.chat.completions
    with patch.object(mock_target, "create", new_callable=AsyncMock) as m_create:
        from openai import OpenAIError
        m_create.side_effect = OpenAIError("auth error")
        with pytest.raises(LLMProviderError, match="OpenAI API error"):
            await provider.generate("test prompt")
