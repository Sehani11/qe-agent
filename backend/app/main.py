"""FastAPI application entry point.

Initializes the FastAPI app, registers routers, and configures
the global exception handler for consistent error responses.
"""

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

# Uvicorn only adds handlers to its own loggers; attach one to app.* so our
# INFO-level logs actually reach the console.
_app_logger = logging.getLogger("app")
if not _app_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _app_logger.addHandler(_handler)
_app_logger.setLevel(logging.INFO)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.services import training_run_service

logger = logging.getLogger(__name__)

#: How long startup will wait on the orphaned-run reconciliation before giving
#: up on it. Generous for the single UPDATE it performs, and short enough that
#: an unreachable database costs seconds rather than asyncpg's 60s connect
#: timeout.
_STARTUP_RECONCILE_TIMEOUT_SECONDS = 10


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup/shutdown events."""
    # Startup
    #
    # A training run lives in a detached task and a subprocess, and neither
    # survives this process exiting. Its ROW does, so a restart mid-run leaves
    # it active forever and the one-at-a-time guard then refuses every later
    # run. Startup is the one moment we know none of our workers is running.
    #
    # Never fatal, and never slow. Before this, startup touched no database at
    # all: the app bound its port immediately and /health answered even with
    # the database down. Reconciling is not worth giving that up — a container
    # that does not bind fast enough fails its health check and gets killed —
    # so it is bounded as well as guarded. The only cost of skipping is a row
    # left active, which the next successful startup clears.
    try:
        await asyncio.wait_for(
            training_run_service.abandon_orphaned_runs(),
            timeout=_STARTUP_RECONCILE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Timed out reconciling orphaned training runs; starting anyway"
        )
    except Exception:
        logger.exception("Could not reconcile orphaned training runs at startup")

    yield
    # Shutdown


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    openapi_url: str | None = None if settings.disable_docs else "/openapi.json"

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    # CORS middleware
    cors_origins = [
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Standard FastAPI HTTP Exceptions
    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        """Return a structured error for HTTP exceptions."""
        if exc.status_code == 401:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "UNAUTHORIZED",
                    "message": "Authentication required.",
                    "code": 401,
                },
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTP_ERROR",
                "message": exc.detail,
                "code": exc.status_code,
            },
        )

    # Validation errors (e.g. bad request body)
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return a structured error for validation failures."""
        return JSONResponse(
            status_code=422,
            content={
                "error": "VALIDATION_ERROR",
                "message": "Invalid request payload or parameters",
                "code": 422,
                "details": [
                    {k: v for k, v in err.items() if k not in ("ctx", "url")}
                    for err in exc.errors()
                ],
            },
        )

    # Global exception handler — consistent error response envelope (NFR-S7)
    @app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Return a safe error response — never expose internal details."""
        return JSONResponse(
            status_code=500,
            content={
                "error": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please try again.",
                "code": 500,
            },
        )

    # Mount API router
    app.include_router(api_router, prefix="/api/v1")

    # Health check endpoint
    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    return app


app: FastAPI = create_app()
