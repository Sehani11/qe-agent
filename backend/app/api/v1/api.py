"""API Router for v1 endpoints.

All v1 routes are included here.
"""

from fastapi import APIRouter

from app.api.v1 import bdd
from app.api.v1 import ingestion
from app.api.v1 import sessions
from app.api.v1 import verification

api_router = APIRouter()

api_router.include_router(bdd.router, prefix="/bdd", tags=["bdd"])
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(verification.router, prefix="/verification", tags=["verification"])
