"""Fine-tuned model provider for BDD generation (Phase 2).

Calls a fine-tuned domain-specific model endpoint trained on real
story-to-test-case pairs. When the fine-tuned model is unavailable,
logs a warning and falls back to the GeneralLLMFallbackProvider.

This provider is activated when BDD_MODEL_PROVIDER=fine_tuned.
"""

import json
import logging

import httpx

from app.core.config import settings
from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError

logger = logging.getLogger(__name__)


class FineTunedModelProvider(BDDModelProvider):
    """BDD generation via a fine-tuned domain-specific model.

    Phase 2 implementation — requires a deployed fine-tuned model
    accessible via HTTP endpoint (FINE_TUNED_MODEL_ENDPOINT).
    """

    def __init__(self) -> None:
        self._endpoint = settings.fine_tuned_model_endpoint
        self._api_key = settings.fine_tuned_model_api_key

        if not self._endpoint:
            logger.warning(
                "FINE_TUNED_MODEL_ENDPOINT is not configured. "
                "Fine-tuned model calls will fail. Consider using "
                "BDD_MODEL_PROVIDER=general_llm as fallback."
            )

    async def generate_bdd(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Generate BDD scenarios using the fine-tuned model endpoint.

        Falls back to GeneralLLMFallbackProvider if the fine-tuned
        model endpoint is unavailable or returns an error.
        """
        if not self._endpoint:
            return await self._fallback(acceptance_criteria, system_prompt, response_format)

        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            payload: dict[str, object] = {
                "acceptance_criteria": acceptance_criteria,
            }
            if system_prompt:
                payload["system_prompt"] = system_prompt
            if response_format:
                payload["response_format"] = response_format

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self._endpoint,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()

        except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError) as e:
            logger.warning(
                "Fine-tuned model endpoint failed (%s). Falling back to general LLM.",
                str(e),
            )
            return await self._fallback(acceptance_criteria, system_prompt, response_format)

    async def _fallback(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Fallback to general LLM when fine-tuned model is unavailable."""
        from app.services.bdd_model.general_llm_fallback import GeneralLLMFallbackProvider

        logger.info("Using GeneralLLMFallbackProvider as fine-tuned model fallback.")
        fallback = GeneralLLMFallbackProvider()
        try:
            return await fallback.generate_bdd(
                acceptance_criteria=acceptance_criteria,
                system_prompt=system_prompt,
                response_format=response_format,
            )
        except Exception as e:
            raise BDDModelProviderError(
                f"Both fine-tuned model and LLM fallback failed: {e!s}"
            ) from e
