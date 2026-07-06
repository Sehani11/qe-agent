"""BDD Model Provider abstract base class.

All BDD generation interactions MUST go through this interface.
Never call LLM SDKs or fine-tuned model endpoints directly in
business logic for BDD generation.
"""

from abc import ABC, abstractmethod


class BDDModelProvider(ABC):
    """Abstract base class for BDD generation model integrations.

    Concrete implementations:
      - GeneralLLMFallbackProvider: delegates to the existing LLMProvider (MVP default)
      - FineTunedModelProvider: calls a fine-tuned model endpoint (Phase 2)

    The bdd_service.py routes through this interface — never calling
    LLM or fine-tuned model directly.
    """

    @abstractmethod
    async def generate_bdd(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate BDD scenarios from acceptance criteria.

        Args:
            acceptance_criteria: The raw AC text from the Jira ticket.
            system_prompt: Optional system-level instructions for generation.
            response_format: Optional JSON schema for structured output.

        Returns:
            Parsed JSON response containing BDD scenarios as a dictionary.

        Raises:
            BDDModelProviderError: If the generation call fails.
        """


class BDDModelProviderError(Exception):
    """Raised when a BDD model provider call fails."""
