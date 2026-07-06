"""LLM provider factory.

Reads the LLM_PROVIDER environment variable and returns the appropriate
LLMProvider concrete implementation. This is the ONLY place provider
selection logic lives.
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


def get_llm_provider() -> LLMProvider:
    """Create and return the configured LLM provider instance.

    Reads `LLM_PROVIDER` from settings and instantiates the matching
    concrete provider class with provider-specific configuration.

    Returns:
        A concrete LLMProvider instance.

    Raises:
        LLMProviderError: If the configured provider is not supported.
    """
    provider_name = settings.llm_provider.lower()
    provider_class = _PROVIDERS.get(provider_name)

    if provider_class is None:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise LLMProviderError(
            f"Unsupported LLM provider: '{provider_name}'. "
            f"Supported providers: {supported}"
        )

    if provider_name in {"local", "ollama"}:
        return provider_class(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
        )

    return provider_class(api_key=settings.llm_api_key)
