"""BDD model provider factory.

Reads the BDD_MODEL_PROVIDER environment variable and returns the
appropriate BDDModelProvider concrete implementation. This is the
ONLY place BDD model provider selection logic lives.
"""

from app.core.config import settings
from app.services.bdd_model.fine_tuned_provider import FineTunedModelProvider
from app.services.bdd_model.general_llm_fallback import GeneralLLMFallbackProvider
from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError

_PROVIDERS: dict[str, type[BDDModelProvider]] = {
    "general_llm": GeneralLLMFallbackProvider,
    "fine_tuned": FineTunedModelProvider,
}


def get_bdd_model_provider() -> BDDModelProvider:
    """Create and return the configured BDD model provider instance.

    Reads `BDD_MODEL_PROVIDER` from settings and instantiates the
    matching concrete provider class.

    Returns:
        A concrete BDDModelProvider instance.

    Raises:
        BDDModelProviderError: If the configured provider is not supported.
    """
    provider_name = settings.bdd_model_provider.lower()
    provider_class = _PROVIDERS.get(provider_name)

    if provider_class is None:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise BDDModelProviderError(
            f"Unsupported BDD model provider: '{provider_name}'. "
            f"Supported providers: {supported}"
        )

    return provider_class()
