"""Turn a request's LLM selection into a provider instance, or a 400.

Lives in the API layer because the failure it handles is a client one: an
unknown provider name arrives from the request body, so it must surface as
"you asked for something that does not exist" (400) rather than as the 500 a
raw LLMProviderError would produce through the global handler.
"""

from typing import Protocol

from fastapi import HTTPException

from app.services.bdd_model.factory import SUPPORTED_BDD_PROVIDERS
from app.services.llm.factory import SUPPORTED_PROVIDERS, get_llm_provider
from app.services.llm.provider import LLMProvider, LLMProviderError


class LLMSelection(Protocol):
    """Structural type for any request body carrying a model choice.

    A Protocol rather than `LLMSelectionMixin` itself because
    `BDDGenerateRequest` cannot inherit the mixin — `app/schemas/bdd.py` is
    loaded standalone by the training serving shim and so may not import from
    sibling schema modules. It declares the same two fields inline, and matches
    here structurally.
    """

    llm_provider: str | None
    llm_model: str | None


def llm_for(selection: LLMSelection) -> LLMProvider:
    """Build the LLM provider a request asked for.

    Args:
        selection: Any request model carrying `llm_provider` / `llm_model`.
            Both unset means "use the server defaults".

    Returns:
        A concrete LLMProvider.

    Raises:
        HTTPException: 400 when the provider name is unsupported, or when the
            selected provider has no credentials configured — both are things
            the caller can act on, unlike a mid-generation provider failure.
    """
    try:
        return get_llm_provider(selection.llm_provider, selection.llm_model)
    except LLMProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _require_supported(
    name: str | None, supported: tuple[str, ...], label: str
) -> None:
    """Reject a provider name the registry does not know.

    Both selection axes normalise and report the same way, so they share this
    rather than keeping two copies that have to be changed together — the two
    differ only in which registry they check and what the message calls it.

    An unset name is not an error: it means "use the server default".

    Raises:
        HTTPException: 400 if `name` is set and unsupported.
    """
    requested = (name or "").lower().strip()
    if requested and requested not in supported:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported {label}: '{requested}'. "
                f"Supported providers: {', '.join(supported)}"
            ),
        )


def validate_selection(selection: LLMSelection) -> None:
    """Reject an unknown provider name before any generation work starts.

    For endpoints that do not build the LLMProvider themselves — BDD generation
    hands the selection to the BDD provider layer, which resolves it several
    calls deep, where an unknown name surfaces as a generation failure (500)
    long after the request was already the caller's mistake.

    Raises:
        HTTPException: 400 if `llm_provider` names a provider that does not exist.
    """
    _require_supported(selection.llm_provider, SUPPORTED_PROVIDERS, "LLM provider")


def llm_with_tools_for(selection: LLMSelection) -> LLMProvider:
    """Build the provider for a flow that requires tool calling.

    Agentic verification is built entirely on `generate_with_tools`. A provider
    that does not implement it fails per scenario, and the service catches that,
    emits an error event and carries on — so the run still ends with a
    `complete` summary and looks like it worked. Refusing here turns that into
    one clear 400 before any of it starts.

    Raises:
        HTTPException: 400 if the selected provider cannot call tools.
    """
    provider = llm_for(selection)
    if not provider.supports_tools:
        name = selection.llm_provider or "The configured provider"
        raise HTTPException(
            status_code=400,
            detail=(
                f"{name} cannot run verification — it does not support the tool "
                "calling this flow requires. Choose an OpenAI or Claude model."
            ),
        )
    return provider


def validate_bdd_selection(bdd_model_provider: str | None) -> None:
    """Reject an unknown BDD provider name before generation starts.

    Same reasoning as `validate_selection`: the name is resolved several calls
    deep, where it surfaces as a generation failure (500) rather than as the
    bad request it is.

    Raises:
        HTTPException: 400 if the name is not a supported BDD provider.
    """
    _require_supported(
        bdd_model_provider, SUPPORTED_BDD_PROVIDERS, "BDD model provider"
    )
