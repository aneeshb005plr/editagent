"""
app/database.py

Owns MongoDB connection lifecycle for the app. TWO connections are
maintained, deliberately, for different reasons:

ASYNC client (AsyncMongoClient) - used for everything: our own
repositories, knowledge sync inserts/deletes, session/document
storage. PyMongo's native async API, NOT Motor.

SYNC client (MongoClient) - used ONLY for constructing
MongoDBAtlasVectorSearch for the similarity_search() retrieval path.
There is no async-native variant of MongoDBAtlasVectorSearch itself.
Calls through this client are wrapped in asyncio.to_thread() at the
call site.

RECONSTRUCTED, NOT VERBATIM: this file was inferred from references
in your real checkpointer.py and main.py, not given directly - diff
against your actual file before trusting this.
"""

import logging

from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient, MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.synchronous.database import Database

from app.config import settings

logger = logging.getLogger("app.database")


async def connect_to_mongo(app: FastAPI) -> None:
    """
    Establishes BOTH the async and sync MongoDB connections. Verifies
    the async connection works by pinging the server - the sync
    client is not separately pinged, since it points at the same
    cluster/URI and a working async ping is sufficient evidence the
    cluster itself is reachable.
    """
    logger.info("Connecting to MongoDB at %s", settings.DB_NAME)

    async_client: AsyncMongoClient = AsyncMongoClient(
        settings.DB_CONNECTION_STRING,
        tz_aware=True,
    )
    sync_client: MongoClient | None = None

    try:
        await async_client.admin.command("ping")

        sync_client = MongoClient(
            settings.DB_CONNECTION_STRING,
            tz_aware=True,
        )

        app.state.mongo_client = async_client
        app.state.mongo_db = async_client[settings.DB_NAME]
        app.state.mongo_sync_client = sync_client
        app.state.mongo_sync_db = sync_client[settings.DB_NAME]

    except Exception:
        await async_client.close()

        if sync_client is not None:
            sync_client.close()

        raise

    logger.info("MongoDB connections established (async + sync)")


async def close_mongo_connection(app: FastAPI) -> None:
    """Closes both connections. Called from the lifespan at shutdown."""

    async_client = getattr(app.state, "mongo_client", None)

    if async_client is not None:
        await async_client.close()
        logger.info("Async MongoDB connection closed")

    sync_client = getattr(app.state, "mongo_sync_client", None)

    if sync_client is not None:
        sync_client.close()
        logger.info("Sync MongoDB connection closed")


def get_database(request: Request) -> AsyncDatabase:
    """
    FastAPI dependency - returns the ASYNC database handle. This is
    what every repository, every job operation, and every chat
    operation should use.
    """
    db = getattr(request.app.state, "mongo_db", None)

    if db is None:
        raise RuntimeError(
            "Database not initialized. "
            "connect_to_mongo() must run during app startup "
            "before any repository can be used."
        )

    return db


def get_sync_database(request: Request) -> Database:
    """
    FastAPI dependency - returns the SYNC database handle. Use ONLY
    for constructing MongoDBAtlasVectorSearch instances for the
    similarity_search() retrieval path (Option B knowledge Q&A, not
    yet built). Never use this for anything else.
    """
    db = getattr(request.app.state, "mongo_sync_db", None)

    if db is None:
        raise RuntimeError(
            "Sync database not initialized. "
            "connect_to_mongo() must run during app startup."
        )

    return db