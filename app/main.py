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
from app.llm import connect_genai, close_genai
from app.jobs.worker import start_worker, stop_worker
from app.jobs.repository import ensure_indexes as ensure_job_indexes
from app.repository.staged_upload_repository import ensure_indexes as ensure_staged_upload_indexes
from app.agent.graph import build_graph

from app.api.v1.router import router


logger = logging.getLogger(__name__)

configure_access_log_filter()


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

        # - Index setup. FATAL on failure, deliberately - both are
        #   real correctness guarantees the application depends on,
        #   not optional performance tuning:
        #   - review_jobs.source_upload_id (unique, partial): the
        #     chat flow's job-creation idempotency guarantee.
        #   - staged_uploads (status, expires_at): supports the
        #     expired-upload cleanup query efficiently as the
        #     collection grows.
        await ensure_job_indexes(app.state.mongo_db)
        await ensure_staged_upload_indexes(app.state.mongo_db)

        # - Checkpointer. Construction is blocking (index creation), so
        #   run it off the event loop.
        await asyncio.to_thread(connect_checkpointer, app)

        app.state.chat_graph = build_graph(app.state.checkpointer)

        connect_genai(app)  # cheap/non-blocking, no need for to_thread

        # start worker
        start_worker(app)

        app.state.ready = True
        logger.info("%s startup complete", settings.AGENT_NAME)

    except Exception:
        logger.exception(
            "Startup failed for %s",
            settings.AGENT_NAME,
        )

        # Best-effort cleanup of whatever partially came up.
        await _safe_async(
            "worker",
            stop_worker(app),
        )

        _safe_sync(
            "checkpointer",
            lambda: close_checkpointer(app),
        )

        await _safe_async(
            "mongo",
            close_mongo_connection(app),
        )

        await _safe_async(
            "genai",
            close_genai(app),
        )

        raise

    yield

    logger.info(
        "Shutting down %s...",
        settings.AGENT_NAME,
    )

    app.state.ready = False

    # Symmetric with startup-abort cleanup above.
    await _safe_async(
        "worker",
        stop_worker(app),
    )

    _safe_sync(
        "checkpointer",
        lambda: close_checkpointer(app),
    )

    await _safe_async(
        "mongo",
        close_mongo_connection(app),
    )

    await _safe_async(
        "genai",
        close_genai(app),
    )

    logger.info(
        "%s shutdown complete",
        settings.AGENT_NAME,
    )


def create_app() -> FastAPI:
    """
    Application factory — builds and returns the configured FastAPI
    instance. Called by uvicorn in root main.py; also called in tests to
    get a fresh app per test run.
    """

    enable_docs = settings.ENABLE_SWAGGER and not settings.IS_PRODUCTION

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