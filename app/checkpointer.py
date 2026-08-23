# app/checkpointer.py

#
# app/checkpointer.py
#
# Owns the LangGraph MongoDB checkpointer lifecycle.
#
# DELIBERATE: this opens its OWN synchronous MongoClient, separate from
# the two clients in app/database.py. MongoDBSaver requires a synchronous
# pymongo client - its async methods (aput/aget/etc.) internally wrap the
# sync client in run_in_executor; there is NO async-native variant. This
# is the SECOND sanctioned sync client in the codebase (the first being
# database.py's vector-search client - see that file's note).
#
# Why a dedicated client rather than reusing app.state.mongo_sync_client:
#   The checkpointer gets its own small connection pool (maxPoolSize=5),
#   so heavy graph-checkpointing traffic cannot starve the pool the
#   vector-search path relies on, and its lifecycle stays self-contained.
#
# Construction is BLOCKING (MongoDBSaver builds its compound indexes on
# init via list_indexes()/create_index()), so connect_checkpointer is a
# plain sync function and is called via asyncio.to_thread() from the
# lifespan so it cannot block the event loop.
#

import logging

from fastapi import FastAPI, Request
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.config import settings


logger = logging.getLogger("app.checkpointer")


def connect_checkpointer(app: FastAPI) -> None:
    """
    Construct the MongoDBSaver checkpointer and store it (plus its
    dedicated sync client) on app.state.

    BLOCKING - MongoDBSaver creates its compound indexes during
    construction. Call this via asyncio.to_thread() from the lifespan.

    Not separately pinged: it points at the same cluster as the async
    client in database.py, whose startup ping already proved the cluster
    is reachable. If index creation itself fails for another reason, that
    exception surfaces here and aborts startup.
    """

    sync_client: MongoClient = MongoClient(
        settings.DB_CONNECTION_STRING,
        maxPoolSize=5,
    )

    try:
        checkpointer = MongoDBSaver(
            client=sync_client,
            db_name=settings.DB_NAME,
        )
    except Exception:
        # Startup failed before we stored the client on app.state, so the
        # lifespan cleanup won't see it - close it here to avoid a leak.
        sync_client.close()
        raise

    app.state.checkpointer = checkpointer
    app.state.checkpointer_sync_client = sync_client

    logger.info(
        "MongoDBSaver checkpointer ready (indexes auto-created)"
    )


def close_checkpointer(app: FastAPI) -> None:
    """Close the checkpointer's dedicated sync client. Idempotent."""

    sync_client = getattr(
        app.state,
        "checkpointer_sync_client",
        None,
    )

    if sync_client is not None:
        sync_client.close()
        app.state.checkpointer_sync_client = None
        app.state.checkpointer = None

        logger.info(
            "MongoDBSaver checkpointer sync client closed"
        )


def get_checkpointer(request: Request) -> MongoDBSaver:
    """
    FastAPI dependency - return the checkpointer from app.state.

    Signature matches get_database()/get_sync_database() in database.py
    (takes a Request), so it can be used directly with Depends().
    Graph-building code invoked outside a request can pass request.app
    or reach app.state however it already holds the app.
    """

    checkpointer = getattr(
        request.app.state,
        "checkpointer",
        None,
    )

    if checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized. "
            "connect_checkpointer() must run during app startup."
        )

    return checkpointer