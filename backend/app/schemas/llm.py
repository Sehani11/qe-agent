"""Per-request LLM selection, shared by every endpoint that calls a model.

The frontend model picker sends the chosen provider/model with each request
rather than mutating server state, so two users (or two tabs) can run different
models concurrently without interfering with each other. Omitting the fields
keeps the server defaults (`LLM_PROVIDER` / `LLM_MODEL`), which is what any
caller with no UI behind it gets.
"""

from pydantic import BaseModel, Field


class LLMSelectionMixin(BaseModel):
    """Optional provider/model override for a single request.

    Both fields are optional and validated in the factory, not here: the set of
    supported providers belongs to the factory, and duplicating it in a schema
    validator is how the two drift apart.
    """

    llm_provider: str | None = Field(
        default=None,
        description=(
            "LLM provider for this request: 'openai', 'claude', or "
            "'local'/'ollama'. Omit to use the server default."
        ),
    )
    llm_model: str | None = Field(
        default=None,
        description=(
            "Model identifier for this request, e.g. 'gpt-4o'. Must belong to "
            "llm_provider. Omit to use that provider's configured default."
        ),
    )
