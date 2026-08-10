"""
app/llm.py

Owns the shared GenAI (LangChain ChatOpenAI) client lifecycle -
same pattern as app/database.py and app/checkpointer.py: constructed
once at startup, stored on app.state, reused across every request
rather than rebuilt per call.

WHY A SINGLE SHARED CLIENT MATTERS HERE, NOT JUST STYLE CONSISTENCY:
ChatOpenAI wraps openai.AsyncOpenAI internally (confirmed via
llm.root_async_client), which owns a real httpx connection pool to
the GenAI shared service. Constructing a fresh ChatOpenAI per call
(as the original image_extraction.py draft did via
build_vision_model()) means a fresh TCP/TLS handshake per image, per
document, at 100MB scale - genuinely wasteful and slower than
reusing one pooled connection. This module fixes that.

USE-CASE-SPECIFIC PARAMETERS (temperature, max_tokens) are applied
via .bind() on the shared client, NOT by constructing separate
ChatOpenAI instances per use case - confirmed .bind() returns a
Runnable that still exposes .ainvoke() and shares the SAME
underlying client/connection pool, so image extraction, future rules
engine calls, etc. all reuse one pool while getting their own
per-call-shape parameters.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger("app.llm")


def connect_genai(app: FastAPI) -> None:
    """Constructs the shared GenAI client and stores it on app.state.

    Fails fast if required GenAI settings are missing - mirrors
    connect_to_mongo()'s guard style, rather than letting a vague
    ChatOpenAI construction error surface later, out of context, on
    the first real request.
    """

    if not settings.GENAI_BASE_URL:
        raise RuntimeError(
            "GENAI_BASE_URL is not configured - cannot construct GenAI client."
        )

    if not settings.GENAI_API_KEY or not settings.GENAI_API_KEY.get_secret_value():
        raise RuntimeError(
            "GENAI_API_KEY is not configured - cannot construct GenAI client."
        )

    logger.info(
        "Connecting to GenAI shared service [model=%s, base_url=%s]",
        settings.GENAI_LLM_MODEL,
        settings.GENAI_BASE_URL,
    )

    client = ChatOpenAI(
        model=settings.GENAI_LLM_MODEL,
        base_url=settings.GENAI_BASE_URL,
        api_key=settings.GENAI_API_KEY.get_secret_value(),
        # Base-level defaults only - use-case-specific overrides
        # (e.g. vision extraction's temperature=0) are applied via
        # .bind() at the point of use, not baked in here.
        max_tokens=settings.GENAI_MAX_TOKENS,
        timeout=60.0,
        max_retries=2,
        # ChatOpenAI's own retry handling covers transient network/
        # rate-limit errors - separate from and in addition to any
        # job-level retry logic the async worker will have.
    )

    app.state.genai_client = client
    logger.info("GenAI client ready")


async def close_genai(app: FastAPI) -> None:
    """Closes the underlying async HTTP client. Idempotent."""

    client: ChatOpenAI | None = getattr(app.state, "genai_client", None)

    if client is not None:
        await client.root_async_client.close()
        app.state.genai_client = None
        logger.info("GenAI client closed")


def get_genai_client(request: Request) -> ChatOpenAI:
    """FastAPI dependency - the shared, unbound GenAI client. Use
    this directly for calls happy with the app-wide defaults set in
    connect_genai(); use get_vision_model() (or your own .bind() call
    against this) for calls needing different parameters."""

    client = getattr(request.app.state, "genai_client", None)

    if client is None:
        raise RuntimeError(
            "GenAI client not initialized. "
            "connect_genai() must run during app startup."
        )

    return client


def get_vision_model(request: Request) -> Runnable:
    """FastAPI dependency - the shared client bound with the
    parameters image_extraction.py needs (temperature=0 for
    deterministic extraction, a lower max_tokens ceiling than the
    general default since extracted text from a single image is
    bounded). Same underlying connection pool as get_genai_client()
    - .bind() does not open a new client."""

    return get_genai_client(request).bind(temperature=0, max_tokens=1000)