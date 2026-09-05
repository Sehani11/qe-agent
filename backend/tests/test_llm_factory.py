"""Tests for the LLM provider factory and interface."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.claude_provider import (
    DEFAULT_CLAUDE_MODEL,
    ClaudeProvider,
    _accepts_sampling_params,
)
from app.services.llm.factory import api_key_for, get_llm_provider
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
    monkeypatch.setattr(
        "app.services.llm.factory.settings.anthropic_api_key", "sk-ant-test"
    )
    provider = get_llm_provider()
    assert isinstance(provider, ClaudeProvider)


def test_factory_returns_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should return OpenAIProvider when LLM_PROVIDER=openai."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "sk-test")
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


# ---------------------------------------------------------------------------
# Per-request provider/model override (frontend model picker)
# ---------------------------------------------------------------------------


def test_explicit_provider_overrides_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit argument wins over LLM_PROVIDER."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.anthropic_api_key", "anthropic-key"
    )
    assert isinstance(get_llm_provider("claude"), ClaudeProvider)


def test_explicit_model_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The requested model is what the provider will send, not LLM_MODEL."""
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "openai-key")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.anthropic_api_key", "anthropic-key"
    )
    monkeypatch.setattr("app.services.llm.openai_provider.settings.llm_model", "gpt-4o")

    assert get_llm_provider("openai", "gpt-4o-mini").model == "gpt-4o-mini"
    assert get_llm_provider("claude", "claude-sonnet-4-5").model == "claude-sonnet-4-5"


def test_omitted_model_falls_back_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each provider supplies its own default — LLM_MODEL is OpenAI's only."""
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "openai-key")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.anthropic_api_key", "anthropic-key"
    )
    monkeypatch.setattr("app.services.llm.openai_provider.settings.llm_model", "gpt-4o")
    monkeypatch.setattr(
        "app.services.llm.factory.settings.ollama_model", "llama3.2:3b"
    )

    assert get_llm_provider("openai").model == "gpt-4o"
    assert get_llm_provider("claude").model == DEFAULT_CLAUDE_MODEL
    assert get_llm_provider("local").model == "llama3.2:3b"


def test_blank_selection_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that sends the fields but leaves them empty gets the defaults.

    Empty strings are what an uninitialised picker sends; reading "" as a model
    named "" would push a guaranteed API rejection to the provider.
    """
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "sk-test")
    monkeypatch.setattr("app.services.llm.openai_provider.settings.llm_model", "gpt-4o")

    provider = get_llm_provider("", "")
    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o"


def test_unsupported_requested_provider_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown name from a request is rejected, not silently defaulted."""
    monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
    with pytest.raises(LLMProviderError, match="Unsupported LLM provider"):
        get_llm_provider("gemini")


#: What an env var set to a blank looks like. Each is FALSE as a credential and
#: TRUE to Python, which is the whole problem.
BLANK_KEYS = ["", " ", "\n", "\t", "  \t\n "]


class TestWhitespaceKeysCountAsAbsent:
    """A blank env var is not a key, but it IS truthy.

    Left unstripped it satisfies every "is a key configured" check, then fails
    deep inside the vendor SDK as `Illegal header value b'Bearer '` — a message
    naming neither the setting nor the vendor.
    """

    @pytest.mark.parametrize("blank", BLANK_KEYS)
    def test_a_blank_key_reads_as_no_key(
        self, monkeypatch: pytest.MonkeyPatch, blank: str
    ) -> None:
        monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", blank)

        assert api_key_for("openai") == ""

    @pytest.mark.parametrize("blank", BLANK_KEYS)
    def test_the_provider_says_what_to_set_rather_than_failing_at_the_header(
        self, blank: str
    ) -> None:
        """The message the user should have seen instead of the httpx error."""
        with pytest.raises(LLMProviderError, match="No OpenAI API key is configured"):
            OpenAIProvider(api_key=blank)

        with pytest.raises(
            LLMProviderError, match="No Anthropic API key is configured"
        ):
            ClaudeProvider(api_key=blank)

    def test_a_real_key_with_stray_whitespace_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A trailing newline from a copy-paste or an editor is survivable, and
        is exactly the character h11 would have rejected in the header."""
        monkeypatch.setattr(
            "app.services.llm.factory.settings.openai_api_key", " sk-real-key\n"
        )

        assert api_key_for("openai") == "sk-real-key"
        assert OpenAIProvider(api_key=" sk-real-key\n")._api_key == "sk-real-key"


class TestOneVariablePerVendor:
    """There is no shared key, which is why none can reach the wrong vendor.

    `LLM_API_KEY` used to serve whichever provider `LLM_PROVIDER` named, making
    the two a pair that had to be changed together. Changing one and not the
    other sent an Anthropic key to api.openai.com — a 401 from an API nobody
    was thinking about, and worst for embeddings, which always call OpenAI
    whatever the chat provider is.
    """

    def test_a_vendors_key_is_read_only_from_its_own_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.llm.factory.settings.openai_api_key", "sk-openai"
        )
        monkeypatch.setattr(
            "app.services.llm.factory.settings.anthropic_api_key", "sk-ant-key"
        )

        assert api_key_for("openai") == "sk-openai"
        assert api_key_for("claude") == "sk-ant-key"

    def test_one_vendors_key_never_stands_in_for_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property the removed fallback used to break."""
        monkeypatch.setattr(
            "app.services.llm.factory.settings.openai_api_key", "sk-openai"
        )
        monkeypatch.setattr("app.services.llm.factory.settings.anthropic_api_key", "")
        # LLM_PROVIDER no longer selects a key for anyone.
        monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")

        assert api_key_for("claude") == ""

    def test_an_unknown_provider_has_no_key(self) -> None:
        assert api_key_for("gemini") == ""


class TestSamplingParamsFollowTheModel:
    """Anthropic removed `temperature` from 4.7 onwards — it is a 400 there.

    Sending it unconditionally broke every Claude 5 request; dropping it
    outright would change behaviour on the older models this app still offers
    (Haiku 4.5 is in the picker, and DEFAULT_CLAUDE_MODEL is a 3.5). So the
    parameter follows the model.
    """

    @pytest.mark.parametrize(
        "model",
        [
            "claude-sonnet-5",
            "claude-opus-5",
            "claude-fable-5",
            "claude-opus-4-8",
            "claude-opus-4-7",
        ],
    )
    def test_removed_on_47_and_later(self, model: str) -> None:
        assert _accepts_sampling_params(model) is False

    @pytest.mark.parametrize(
        "model",
        [
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-haiku-4-5-20251001",
            DEFAULT_CLAUDE_MODEL,
            "claude-3-opus-20240229",
        ],
    )
    def test_still_accepted_on_46_and_earlier(self, model: str) -> None:
        # A pinned id carries a release date; the generation is what matters.
        assert _accepts_sampling_params(model) is True

    @pytest.mark.parametrize("model", ["", "some-other-model", "claude"])
    def test_an_unrecognised_model_is_assumed_to_have_dropped_it(
        self, model: str
    ) -> None:
        """Omitting the parameter works everywhere; sending it to a model that
        dropped it fails the whole request. Every generation has removed more,
        so guessing "supported" for an unknown name is the guess that breaks."""
        assert _accepts_sampling_params(model) is False

    @pytest.mark.asyncio
    async def test_the_request_omits_temperature_for_a_5_model(self) -> None:
        provider = ClaudeProvider(api_key="sk-ant-test", model="claude-sonnet-5")

        with patch.object(
            provider._client.messages, "create", new_callable=AsyncMock
        ) as create:
            create.return_value = MagicMock(content=[MagicMock(text="hi")])
            await provider.generate("hello")

        assert "temperature" not in create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_the_request_still_sends_it_for_an_older_model(self) -> None:
        provider = ClaudeProvider(api_key="sk-ant-test", model="claude-haiku-4-5")

        with patch.object(
            provider._client.messages, "create", new_callable=AsyncMock
        ) as create:
            create.return_value = MagicMock(content=[MagicMock(text="hi")])
            await provider.generate("hello")

        assert create.call_args.kwargs["temperature"] == 0.7
