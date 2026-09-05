"""BDD generation service.

Coordinates the translation of raw Jira acceptance criteria into structured
Gherkin behavior-driven development scenarios via the configured BDD model
provider (fine-tuned model or general LLM fallback).

Architecture rule: BDD generation calls go through the BDDModelProvider
interface — never directly through LLMProvider or fine-tuned model SDK.
"""

import logging
from uuid import UUID

from app.schemas.bdd import BDDGenerateResponse
from app.services.bdd_model.factory import get_bdd_model_provider
from app.services.bdd_model.provider import BDDModelProvider, BDDModelProviderError

logger = logging.getLogger(__name__)


class BDDServiceError(Exception):
    """Raised when BDD generation fails at the service level."""


# System prompt used for BDD generation (shared between all providers)
BDD_SYSTEM_PROMPT = (
    "You are an expert Quality Assurance Engineer and Technical Analyst. "
    "Your goal is to parse raw Jira Acceptance Criteria (AC) and output them "
    "as clean, valid Gherkin BDD scenarios.\n"
    "Requirements:\n"
    "- You MUST generate at least one scenario. Never return an empty scenarios array.\n"
    "- If the input lacks explicit AC, infer reasonable scenarios from the description or summary.\n"
    "- Group related scenarios under a logical 'feature' name.\n"
    "- Format scenarios using Given, When, Then syntax.\n"
    "- You MUST provide exact traceability linking your scenario to its 'source_ac_clause'.\n"
    "- Include 'And' and 'But' keywords logically inside the Given/When/Then text blocks if they happen.\n"
    # Completeness. A clause with no scenario is invisible downstream: the
    # scenario that was never written cannot fail verification, so the report
    # reads as though the ticket were smaller than it is.
    "- COMPLETENESS: every numbered clause in the input MUST be cited by at "
    "least one scenario. Before answering, check your scenarios against the "
    "input clause by clause and add one for any you have not covered. Covering "
    "some clauses thoroughly does not make up for skipping others.\n"
    # Precedence. Tickets routinely carry a rules list AND an acceptance
    # criteria list; treating either as the whole ticket drops the other.
    "- PRECEDENCE: when the input contains both a rules-style list (e.g. "
    "'Business Rules') and an explicit 'Acceptance Criteria' list, cover BOTH. "
    "They are not restatements of each other — the criteria list usually adds "
    "UI- and API-level behaviour the rules do not mention. Cite whichever list "
    "a scenario came from in 'source_ac_clause'.\n"
    # Distinctness. Duplicate scenarios inflate the apparent scenario count and
    # make coverage look broader than it is.
    "- DISTINCTNESS: never emit two scenarios that assert the same behaviour "
    "with reworded steps. If two clauses imply the same test, write it once "
    "and cite the clause it belongs to most directly.\n"
    # Executability. Alternation inside a step hides several cases in one
    # scenario and cannot be run by any Gherkin runner.
    "- Each scenario MUST cover ONE case. Never write alternation into a step "
    "('completed, rejected, or cancelled'; 'more or less than 24 hours'). Split "
    "it into one scenario per case.\n"
    # Observability. A Then nobody can check is not a test.
    "- Every 'then' MUST state an observable, checkable outcome — a status "
    "value, a stored field, an HTTP code, a rendered element. 'the request is "
    "processed' or 'it works correctly' are not acceptable.\n"
)


async def generate_bdd_scenarios(
    session_id: UUID,
    acceptance_criteria: str,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    bdd_model_provider: str | None = None,
) -> BDDGenerateResponse:
    """Generate structured BDD scenarios from acceptance criteria.

    Routes through the configured BDDModelProvider (fine-tuned model or
    general LLM fallback) based on the BDD_MODEL_PROVIDER env var.

    Args:
        session_id: Unique ID for the current user's session.
        acceptance_criteria: The raw text of the Jira ticket's acceptance criteria.
        llm_provider: General-LLM provider selected for this request. Only takes
            effect on the general-LLM path; the fine-tuned endpoint serves one
            fixed model regardless. None means the server default.
        llm_model: Model identifier to pair with `llm_provider`.
        bdd_model_provider: "fine_tuned" or "general_llm" for this request.
            None means the server default. `llm_provider`/`llm_model` only
            reach the model on the general_llm path — the fine-tuned endpoint
            serves one fixed model.

    Returns:
        Structured Pydantic response containing the parsed BDD scenarios.

    Raises:
        BDDServiceError: If the model call fails or returns improperly formatted data.
    """
    provider = get_bdd_model_provider(
        provider=bdd_model_provider,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )

    # Use Pydantic's JSON schema for type constraint hinting to the model
    response_schema = BDDGenerateResponse.model_json_schema()

    try:
        raw_json_dict = await provider.generate_bdd(
            acceptance_criteria=acceptance_criteria,
            system_prompt=BDD_SYSTEM_PROMPT,
            response_format=response_schema,
        )

        # Deserialize JSON dict into the strict Pydantic model
        response_model = BDDGenerateResponse.model_validate(raw_json_dict)
        return response_model

    except BDDModelProviderError as e:
        raise BDDServiceError(f"BDD generation failed: {e!s}") from e
    except Exception as e:
        raise BDDServiceError(f"Failed to parse or validate model output: {e!s}") from e
    finally:
        _log_attribution(provider, session_id)


def _log_attribution(provider: BDDModelProvider, session_id: UUID) -> None:
    """Emit exactly one attribution line per generation, keyed by session_id.

    Runs for successful AND failed generations. `configured` is what
    BDD_MODEL_PROVIDER selected; `effective` is what actually produced the
    output — they differ when a provider silently degraded, and the pair is
    what makes Story 6.3's model comparison trustworthy. The session_id is
    the join key: without it, concurrent generations cannot be attributed.
    """
    logger.info(
        "bdd_model.generation session_id=%s configured=%s effective=%s reason=%s",
        session_id,
        provider.name,
        provider.effective_provider or "none",
        provider.fallback_reason or "none",
    )
