# app/main.py

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import connect_to_mongo, close_mongo_connection
from app.checkpointer import connect_checkpointer, close_checkpointer
from app.utils.error_handlers import register_error_handlers
from app.utils.access_log_filter import configure_access_log_filter
from app.api.v1.router import router
from app.llm import connect_genai, close_genai


logger = logging.getLogger(__name__)

configure_access_log_filter()


# - Guarded cleanup helpers (module-level, shared by startup-abort and
#   shutdown paths). Each step is independently guarded so one failure
#   can't mask the original error or skip the remaining cleanups.

async def _safe_async(name: str, coro) -> None:
    try:
        await coro
    except Exception:
        logger.exception("Cleanup of %s failed", name)


def _safe_sync(name: str, fn) -> None:
    try:
        fn()
    except Exception:
        logger.exception("Cleanup of %s failed", name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Starting %s [env=%s]",
        settings.AGENT_NAME,
        settings.ENVIRONMENT,
    )

    try:
        # - MongoDB (async + sync-for-vector-search clients)
        await connect_to_mongo(app)

        # - Checkpointer. Construction is blocking (index creation), so
        #   run it off the event loop.
        await asyncio.to_thread(connect_checkpointer, app)
        connect_genai(app)  # cheap/non-blocking, no need for to_thread


        app.state.ready = True
        logger.info("%s startup complete", settings.AGENT_NAME)

    except Exception:
        logger.exception(
            "Startup failed for %s",
            settings.AGENT_NAME,
        )

        # Best-effort cleanup of whatever partially came up.
        _safe_sync(
            "checkpointer",
            lambda: close_checkpointer(app),
        )
        await _safe_async(
            "mongo",
            close_mongo_connection(app),
        )
        await _safe_async("genai", close_genai(app))

        raise

    yield

    logger.info("Shutting down %s...", settings.AGENT_NAME)
    app.state.ready = False

    # Symmetric with startup-abort cleanup above.
    _safe_sync(
        "checkpointer",
        lambda: close_checkpointer(app),
    )
    await _safe_async(
        "mongo",
        close_mongo_connection(app),
    )

    logger.info(
        "%s shutdown complete",
        settings.AGENT_NAME,
    )


def create_app() -> FastAPI:
    """
    Application factory - builds and returns the configured FastAPI
    instance. Called by uvicorn in root main.py; also called in tests to
    get a fresh app per test run.
    """

    enable_docs = (
        settings.ENABLE_SWAGGER
        and not settings.IS_PRODUCTION
    )

    app = FastAPI(
        title=settings.AGENT_NAME,
        lifespan=lifespan,
        docs_url="/docs" if enable_docs else None,
        redoc_url="/redoc" if enable_docs else None,
        openapi_url="/openapi.json" if enable_docs else None,
    )

    # Initial state - flipped to True at end of startup.
    app.state.ready = False
    app.state.post_upload_hook = None  # set by upload handler; consumed post-upload

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(router)

    return app