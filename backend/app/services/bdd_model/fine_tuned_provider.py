"""Fine-tuned model provider for BDD generation (Phase 2).

Calls a fine-tuned domain-specific model endpoint trained on real
story-to-test-case pairs. When the fine-tuned model is unavailable,
logs a warning and falls back to the GeneralLLMFallbackProvider.

This provider is activated when BDD_MODEL_PROVIDER=fine_tuned.
"""

import asyncio
import json
import logging
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.bdd import BDDGenerateResponse
from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError

logger = logging.getLogger(__name__)


class _InvalidEndpointResponseError(Exception):
    """The endpoint answered, but with something BDDGenerateResponse cannot use.

    Private to this module: it never escapes generate_bdd, which converts it
    into a fallback. It exists only to keep "endpoint misbehaving" on a
    separate branch from "endpoint unreachable" — the two degrade identically
    for the user but call for entirely different fixes, and the attribution
    line is the only place that distinction survives for Story 6.3.
    """


def _redact(url: str) -> str:
    """Strip query string, fragment, and userinfo from an endpoint URL.

    httpx's raise_for_status() embeds the full request URL in the exception
    message, so logging the raw error would write any query-string credentials
    (e.g. ?api-key=...) into the logs. Only scheme/host/port/path survive.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-endpoint>"

    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _require_usable_bdd_response(parsed: object) -> None:
    """Raise unless the endpoint body is something the application can use.

    `bdd_service` feeds this straight into `BDDGenerateResponse.model_validate`,
    so an endpoint that can emit an unparseable shape just moves the failure
    downstream — where it is reported as a model parse error and no fallback
    runs. Checked here instead, where degrading is still possible.
    """
    try:
        validated = BDDGenerateResponse.model_validate(parsed)
    except ValidationError as e:
        raise _InvalidEndpointResponseError(
            f"does not match BDDGenerateResponse ({e.error_count()} errors)"
        ) from e

    # An empty array VALIDATES — the field defaults to [] — and would reach the
    # user as a successful generation containing nothing at all.
    if not validated.scenarios:
        raise _InvalidEndpointResponseError("contained zero scenarios")


def _failure_detail(exc: Exception) -> str:
    """Describe a request failure without echoing the URL or response body."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


class FineTunedModelProvider(BDDModelProvider):
    """BDD generation via a fine-tuned domain-specific model.

    Phase 2 implementation — requires a deployed fine-tuned model
    accessible via HTTP endpoint (FINE_TUNED_MODEL_ENDPOINT).
    """

    name = "fine_tuned"

    def __init__(
        self,
        *,
        allow_fallback: bool = True,
        llm_provider: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._endpoint = settings.fine_tuned_model_endpoint
        self._api_key = settings.fine_tuned_model_api_key
        self._timeout = settings.fine_tuned_model_timeout_seconds
        # Production keeps this True: a user waiting on BDD generation should
        # get scenarios, not an error, when the endpoint is down.
        #
        # A COMPARISON must set it False. Falling back there silently replaces
        # the fine-tuned column with general-LLM output, so the two columns
        # describe one model while appearing to describe two — and the wasted
        # fallback call is paid for only to be discarded.
        self._allow_fallback = allow_fallback
        # Only ever used by _fallback(): the fine-tuned endpoint is its own
        # model and ignores the picker, but the general LLM it degrades to
        # must still honour what the user selected.
        self._llm_provider = llm_provider
        self._llm_model = llm_model

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
            return await self._fallback(
                acceptance_criteria,
                system_prompt,
                response_format,
                reason="endpoint_unset",
            )

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

            # asyncio.timeout bounds the WHOLE exchange. httpx's own timeout is
            # per-phase (connect/read/write/pool each get the full value), so it
            # alone cannot keep the request inside the NFR-P3 budget that the
            # general-LLM fallback still has to fit into afterwards.
            async with asyncio.timeout(self._timeout):
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._endpoint,
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    parsed: dict[str, object] = response.json()

            _require_usable_bdd_response(parsed)

            self.effective_provider = self.name
            return parsed

        except _InvalidEndpointResponseError as e:
            # Story 6.2 AC5. Before this, a wrong-shaped body was returned
            # verbatim and blew up later in bdd_service as a generic parse
            # error — no fallback, and the failure was attributed to the model
            # rather than to the endpoint.
            logger.warning(
                "Fine-tuned model endpoint returned an unusable body (%s) "
                "endpoint=%s. Falling back to general LLM.",
                e,
                _redact(self._endpoint),
            )
            return await self._fallback(
                acceptance_criteria,
                system_prompt,
                response_format,
                reason="endpoint_invalid_response",
            )

        except (
            httpx.HTTPStatusError,
            httpx.RequestError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            TimeoutError,
        ) as e:
            logger.warning(
                "Fine-tuned model endpoint failed (%s) endpoint=%s. "
                "Falling back to general LLM.",
                _failure_detail(e),
                _redact(self._endpoint),
            )
            return await self._fallback(
                acceptance_criteria,
                system_prompt,
                response_format,
                reason="endpoint_error",
            )

    async def _fallback(
        self,
        acceptance_criteria: str,
        system_prompt: str = "",
        response_format: dict[str, object] | None = None,
        reason: str = "unspecified",
    ) -> dict[str, object]:
        """Fallback to general LLM when fine-tuned model is unavailable.

        Records the degradation on this instance so bdd_service can emit a
        single attribution line carrying both the configured and the effective
        provider — letting the Story 6.3 evaluation pipeline tell a genuine
        fine-tuned response apart from general-LLM output returned under the
        fine_tuned label.
        """
        from app.services.bdd_model.general_llm_fallback import (
            GeneralLLMFallbackProvider,
        )

        self.fallback_reason = reason

        # Recorded before raising, so the caller can still report WHY the
        # fine-tuned model did not answer rather than just that it failed.
        if not self._allow_fallback:
            raise BDDModelProviderError(
                f"Fine-tuned model unavailable ({reason}) and fallback is "
                "disabled for this call."
            )

        fallback = GeneralLLMFallbackProvider(self._llm_provider, self._llm_model)
        try:
            result = await fallback.generate_bdd(
                acceptance_criteria=acceptance_criteria,
                system_prompt=system_prompt,
                response_format=response_format,
            )
            self.effective_provider = fallback.name
            return result
        except Exception as e:
            # Logged, not just wrapped. The attribution line reports this as
            # `effective=none reason=<why the FINE-TUNE failed>`, which names
            # the wrong culprit entirely — the fine-tune failing is expected
            # here and survivable, and the general LLM failing behind it is the
            # part that turned a degradation into a 500. Without this the real
            # cause exists only in a response body nobody is reading.
            logger.error(
                "General-LLM fallback ALSO failed after fine-tuned %s: %s",
                reason,
                _failure_detail(e),
                exc_info=True,
            )
            raise BDDModelProviderError(
                f"Both fine-tuned model and LLM fallback failed: {e!s}"
            ) from e
