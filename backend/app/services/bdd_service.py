"""BDD generation service.

Coordinates the translation of raw Jira acceptance criteria into structured
Gherkin behavior-driven development scenarios via the configured BDD model
provider (fine-tuned model or general LLM fallback).

Architecture rule: BDD generation calls go through the BDDModelProvider
interface — never directly through LLMProvider or fine-tuned model SDK.
"""

from uuid import UUID

from app.schemas.bdd import BDDGenerateResponse
from app.services.bdd_model.factory import get_bdd_model_provider
from app.services.bdd_model.provider import BDDModelProviderError


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
)


async def generate_bdd_scenarios(
    session_id: UUID, acceptance_criteria: str
) -> BDDGenerateResponse:
    """Generate structured BDD scenarios from acceptance criteria.

    Routes through the configured BDDModelProvider (fine-tuned model or
    general LLM fallback) based on the BDD_MODEL_PROVIDER env var.

    Args:
        session_id: Unique ID for the current user's session.
        acceptance_criteria: The raw text of the Jira ticket's acceptance criteria.

    Returns:
        Structured Pydantic response containing the parsed BDD scenarios.

    Raises:
        BDDServiceError: If the model call fails or returns improperly formatted data.
    """
    provider = get_bdd_model_provider()

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
