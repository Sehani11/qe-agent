"""Operator policy the UI has to know about.

Only settings whose value changes what a control should DO belong here — not
credentials, model names, or anything a client could act on directly. The one
entry today is the training-data policy: a deployment can forbid training on
captured content, and a consent toggle that stays live under that policy would
be telling the user their choice matters when it cannot.
"""

from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.config import settings
from app.schemas.config import ClientConfigResponse

router = APIRouter()


@router.get("", response_model=ClientConfigResponse)
async def get_client_config(
    current_user: str = Depends(get_current_user),  # noqa: B008
) -> ClientConfigResponse:
    """Return the operator policy flags the client needs to render itself."""
    return ClientConfigResponse(
        training_data_opt_in_allowed=settings.training_data_opt_in,
    )
