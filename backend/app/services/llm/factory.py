"""LLM provider factory.

Returns the appropriate LLMProvider concrete implementation. Selection comes
from the caller when a request carries an explicit provider/model choice, and
otherwise from the LLM_PROVIDER / LLM_MODEL environment variables. This is the
ONLY place provider selection logic lives.
"""

from app.core.config import settings
from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.provider import LLMProvider, LLMProviderError

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "claude": ClaudeProvider,
    "local": OllamaProvider,
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
}

#: Provider names a client is allowed to ask for. "local"/"ollama" are the same
#: implementation under two names, both accepted so an existing LLM_PROVIDER
#: value keeps working.
SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(sorted(_PROVIDERS))


def get_llm_provider(
    provider: str | None = None, model: str | None = None
) -> LLMProvider:
    """Create and return an LLM provider instance.

    Args:
        provider: Provider name to use for this call ("claude", "openai",
            "local"/"ollama"). Falls back to `LLM_PROVIDER` when None or empty,
            which is what background/CLI callers with no request context get.
        model: Model identifier to use for this call. Falls back to the
            provider's own configured default (`LLM_MODEL` for OpenAI,
            `OLLAMA_MODEL` for Ollama, a pinned Sonnet build for Claude).

            A model is only meaningful alongside its own provider — passing an
            OpenAI model name with `provider="claude"` sends that name straight
            to Anthropic, which rejects it. Callers pass the pair, never one
            half of it.

    Returns:
        A concrete LLMProvider instance.

    Raises:
        LLMProviderError: If the requested provider is not supported.
    """
    provider_name = (provider or settings.llm_provider).lower().strip()
    provider_class = _PROVIDERS.get(provider_name)

    if provider_class is None:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise LLMProviderError(
            f"Unsupported LLM provider: '{provider_name}'. "
            f"Supported providers: {supported}"
        )

    # Empty string is treated as "not specified" rather than as a model named
    # "": a client that sends the field but leaves it blank means the default.
    model_name = (model or "").strip() or None

    if provider_name in {"local", "ollama"}:
        return provider_class(
            base_url=settings.ollama_base_url,
            model=model_name or settings.ollama_model,
        )

    return provider_class(api_key=api_key_for(provider_name), model=model_name)


def api_key_for(provider_name: str) -> str:
    """The credential for one vendor, never another's.

    Public because embeddings need it too: `vector_service` always calls
    OpenAI regardless of which chat provider a request selected, so it has to
    resolve the OpenAI credential by the same rules rather than reading a
    setting directly and disagreeing with the factory.

    One variable per vendor and no shared fallback. A key from the wrong vendor
    is worse than no key: it satisfies the provider's "is a key configured"
    check and fails later as a 401, which looks like a broken account instead
    of a missing setting. `LLM_API_KEY` used to be that shared fallback and is
    exactly how a wrong-vendor key got in — it served whichever provider
    `LLM_PROVIDER` named, so the two had to be changed together or not at all.

    Stripped, and whitespace-only counts as absent. An env var set to a blank
    or a trailing space is not a key, but it is truthy: it would satisfy that
    same check and then fail as `Illegal header value b'Bearer '` from deep
    inside the vendor SDK, naming neither the setting nor the vendor.
    """
    return (
        {
            "openai": settings.openai_api_key,
            "claude": settings.anthropic_api_key,
        }
        .get(provider_name, "")
        .strip()
    )
