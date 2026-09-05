"""Schemas for the client-facing configuration endpoint."""

from pydantic import BaseModel, Field


class ClientConfigResponse(BaseModel):
    """Operator policy the UI needs in order to render controls honestly."""

    training_data_opt_in_allowed: bool = Field(
        ...,
        description=(
            "Whether this deployment permits captured BDD content to be used "
            "for fine-tuning at all. False means the per-request consent "
            "toggle cannot grant it, so the UI shows the control as "
            "unavailable rather than off."
        ),
    )
