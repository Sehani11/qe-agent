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

    async def generate_bdd(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate BDD scenarios using the general-purpose LLM."""
        provider = get_llm_provider()

        prompt = (
            f"Generate BDD scenarios for the following acceptance criteria:\n\n"
            f"<acceptance_criteria>\n"
            f"{acceptance_criteria}\n"
            f"</acceptance_criteria>\n"
        )

        try:
            return await provider.generate_structured(
                prompt=prompt,
                system_prompt=system_prompt,
                response_format=response_format,
            )
        except LLMProviderError as e:
            raise BDDModelProviderError(
                f"General LLM BDD generation failed: {e!s}"
            ) from e
