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

#: Provider names a client is allowed to ask for.
SUPPORTED_BDD_PROVIDERS: tuple[str, ...] = tuple(sorted(_PROVIDERS))


def get_bdd_model_provider(
    *,
    provider: str | None = None,
    allow_fallback: bool | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> BDDModelProvider:
    """Create and return the configured BDD model provider instance.

    Instantiates the matching concrete provider class. The choice comes from
    the caller when a request carries one (the per-page model picker), and
    otherwise from `BDD_MODEL_PROVIDER`.

    Args:
        provider: "general_llm" or "fine_tuned" for this call. Falls back to
            `BDD_MODEL_PROVIDER` when None or empty, which is what callers with
            no request context get.
        allow_fallback: Only meaningful for `fine_tuned`. Leave None to take the
            default from `FINE_TUNED_ALLOW_FALLBACK`, which is what the serving
            path does. Pass False explicitly when the caller is MEASURING the
            fine-tuned model — a silent fallback there returns general-LLM
            output under the fine_tuned label, which is indistinguishable from
            a real result. An explicit value always wins over the setting, so a
            measuring caller cannot be re-enabled by a config change.
        llm_provider: General-LLM provider chosen for this request (frontend
            model picker). Applies to `general_llm`, and to the general LLM
            that `fine_tuned` degrades to — never to the fine-tuned endpoint
            itself, which serves one fixed model. None means the server default.
        llm_model: Model identifier to pair with `llm_provider`.

    Returns:
        A concrete BDDModelProvider instance.

    Raises:
        BDDModelProviderError: If the configured provider is not supported.
    """
    provider_name = (provider or settings.bdd_model_provider).lower().strip()
    provider_class = _PROVIDERS.get(provider_name)

    if provider_class is None:
        supported = ", ".join(sorted(_PROVIDERS.keys()))
        raise BDDModelProviderError(
            f"Unsupported BDD model provider: '{provider_name}'. "
            f"Supported providers: {supported}"
        )

    # Explicit rather than passing **kwargs through: the general provider has
    # no fallback to disable, and forwarding an argument it does not accept
    # would turn a serving call into a TypeError.
    if provider_class is FineTunedModelProvider:
        resolved = (
            settings.fine_tuned_allow_fallback
            if allow_fallback is None
            else allow_fallback
        )
        return provider_class(
            allow_fallback=resolved,
            llm_provider=llm_provider,
            llm_model=llm_model,
        )
    return provider_class(llm_provider=llm_provider, llm_model=llm_model)
