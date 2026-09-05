"""API Router for v1 endpoints.

All v1 routes are included here.
"""

from fastapi import APIRouter

from app.api.v1 import (
    bdd,
    chat,
    config,
    evaluation,
    ingestion,
    knowledge,
    projects,
    reports,
    sessions,
    training,
    verification,
)

api_router = APIRouter()

api_router.include_router(bdd.router, prefix="/bdd", tags=["bdd"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(config.router, prefix="/config", tags=["config"])
api_router.include_router(
    evaluation.router, prefix="/evaluation", tags=["evaluation"]
)
api_router.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["knowledge"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(training.router, prefix="/training", tags=["training"])
api_router.include_router(
    verification.router, prefix="/verification", tags=["verification"]
)
