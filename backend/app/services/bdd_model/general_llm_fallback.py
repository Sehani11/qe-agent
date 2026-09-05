"""General LLM fallback provider for BDD generation.

Delegates BDD generation to the existing LLMProvider interface.
This is the MVP default — used when BDD_MODEL_PROVIDER=general_llm
or when the fine-tuned model is unavailable.
"""

from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError
from app.services.llm.factory import get_llm_provider
from app.services.llm.provider import LLMProviderError


class GeneralLLMFallbackProvider(BDDModelProvider):
    """BDD generation via the general-purpose LLM provider.

    Wraps the existing LLMProvider.generate_structured() method,
    translating LLMProviderError into BDDModelProviderError.
    """

    name = "general_llm"

    def __init__(
        self, llm_provider: str | None = None, llm_model: str | None = None
    ) -> None:
        """Record which general LLM to delegate to.

        Args:
            llm_provider: Provider chosen for this request (frontend model
                picker). None means the server default.
            llm_model: Model chosen for this request. None means the provider's
                own default.
        """
        self._llm_provider = llm_provider
        self._llm_model = llm_model

    async def generate_bdd(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate BDD scenarios using the general-purpose LLM."""
        # Built INSIDE the try below, not here. Construction is where a missing
        # or wrong-vendor credential is caught, and an LLMProviderError escaping
        # this method uncaught reaches bdd_service's catch-all, which reports
        # every unknown exception as "Failed to parse or validate model output"
        # — a message about the model's answer, for a call that never happened.
        prompt = (
            f"Generate BDD scenarios for the following acceptance criteria:\n\n"
            f"<acceptance_criteria>\n"
            f"{acceptance_criteria}\n"
            f"</acceptance_criteria>\n"
        )

        try:
            provider = get_llm_provider(self._llm_provider, self._llm_model)
            result = await provider.generate_structured(
                prompt=prompt,
                system_prompt=system_prompt,
                response_format=response_format,
            )
            self.effective_provider = self.name
            return result
        except LLMProviderError as e:
            raise BDDModelProviderError(
                f"General LLM BDD generation failed: {e!s}"
            ) from e
