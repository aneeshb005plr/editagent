# app/config.py
#
# Single source of truth for ALL settings and environment variables.
# Nothing is hardcoded anywhere else in the codebase.
#
# All values come from environment variables - see .env.example
#
# Source precedence: secrets_dir > env vars > .env file > defaults

import json
import logging
import os
from functools import lru_cache
from typing import List, Literal, Optional, Tuple, Type

from pydantic import (
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


DEFAULT_SECRETS_PATH: str = os.environ.get(
    "SECRETS_VOLUME_PATH",
    "/var/app/secrets",
)


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger("app.config")


EnvironmentType = Literal[
    "development",
    "testing",
    "production",
]

AuthModeType = Literal[
    "header",
    "entra",
]
# Matches EnvironmentType's own pattern - an invalid AUTH_MODE now
# fails at STARTUP (Pydantic's own Literal validation), not on the
# first request. Previously a bare `str`, only checked at request
# time inside app/auth/dependencies.py's get_current_user_id() (a
# RuntimeError raised mid-request, not at deploy) - fixed for
# consistency with this file's own established fail-fast philosophy
# (see _validate_required_in_production below, which follows the
# same principle for a different set of fields).


class Settings(BaseSettings):
    """EditEdge application settings - single source of truth."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir=(
            DEFAULT_SECRETS_PATH
            if os.path.isdir(DEFAULT_SECRETS_PATH)
            else None
        ),
        case_sensitive=True,
        extra="ignore",
        enable_decoding=False,
        frozen=True,
    )

    # ------------------------------------------------------------------
    # Environment
    # ------------------------------------------------------------------

    ENVIRONMENT: EnvironmentType = "development"
    DEBUG: bool = False

    PORT: int = Field(
        default=8080,
        ge=1,
        le=65535,
    )

    SECRETS_VOLUME_PATH: str = DEFAULT_SECRETS_PATH

    # ------------------------------------------------------------------
    # Agent Identity
    # ------------------------------------------------------------------

    AGENT_ID: str = "editedge"
    AGENT_NAME: str = "EditEdge"

    # ------------------------------------------------------------------
    # GenAI Shared Service (OpenAI compatible)
    # ------------------------------------------------------------------

    GENAI_BASE_URL: Optional[str] = None

    GENAI_API_KEY: Optional[SecretStr] = None

    GENAI_LLM_MODEL: str = "azure.gpt-4.1"
    # STILL UNCONFIRMED against your real endpoint - every use of
    # this value so far (test scripts, this default) has been a
    # placeholder, never confirmed as the actual model string your
    # GenAI service expects. Verify before relying on this default.

    GENAI_EMBEDDINGS_MODEL: str = (
        "azure.text-embedding-3-small"
    )
    # RESERVED / not yet used by anything built so far - the review
    # engine (app/review/) uses GENAI_LLM_MODEL directly for both
    # judgment/consistency passes and vision extraction; embeddings
    # would only be needed by the future secondary vector-store Q&A
    # feature (style-guide chat), not yet built.

    GENAI_MAX_TOKENS: int = Field(
        default=4096,
        gt=0,
        le=128000,
    )

    # ------------------------------------------------------------------
    # MongoDB
    # ------------------------------------------------------------------

    DB_CONNECTION_STRING: str = (
        "mongodb://localhost:27017"
    )

    DB_NAME: str = "agent_editedge"

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    AUTH_MODE: AuthModeType = "header"
    # "header" (default): reads X-User-Id directly, no cryptographic
    # validation - matches the confirmed reality that Entra auth
    # wasn't implemented in the app this pattern is modeled on.
    # "entra": delegates to the real app/auth/entra.py JWT validation
    # - see app/auth/dependencies.py.

    ENTRA_TENANT_ID: str = ""
    # FIXED REAL ISSUE: previously `str` with NO default, meaning it
    # was unconditionally required - Settings() construction would
    # fail at import time in local/header-mode dev unless this was
    # set, even though AUTH_MODE="header" (the default) doesn't use
    # Entra at all. Now optional by default; _guard_auth below
    # requires it ONLY when AUTH_MODE="entra" is actually selected -
    # same conditional-requirement pattern as
    # _validate_required_in_production uses for production-only
    # fields.

    AUTH_DEV_BYPASS: bool = False
    # Never on in prod - enforced by _guard_auth below. Note this
    # flag lives entirely inside app/auth/entra.py's own
    # decode_token() - confirmed via direct testing that its
    # early-return path also skips the tenant guard check, not just
    # signature verification. Flagged in the architecture doc as a
    # real, undecided question - not something this config change
    # resolves.

    # ------------------------------------------------------------------
    # Document intake / upload behaviour
    # ------------------------------------------------------------------

    MAX_FILE_SIZE_MB: int = Field(
        default=100,
        gt=0,
        # 100MB is a confirmed, non-negotiable product requirement -
        # see architecture doc Section 7. Not a guess.
    )

    SUPPORTED_FILE_EXTENSIONS: List[str] = Field(
        default_factory=lambda: [".docx", ".pptx", ".xlsx", ".pdf"]
        # ODP explicitly out of scope - confirmed in discovery phase.
        #
        # KNOWN REAL DRIFT RISK, not yet fixed: app/documents/
        # dispatcher.py's supported_extensions() is hardcoded from
        # its own _PARSERS dict and does NOT read this setting at
        # all - confirmed by inspecting dispatcher.py directly.
        # Changing this value currently has NO effect on what files
        # the dispatcher actually accepts. dispatcher.py's own
        # docstring already flags this exact risk ("config.
        # SUPPORTED_FILE_EXTENSIONS should be validated against
        # supported_extensions() ... rather than maintained as a
        # second, independent list that can silently drift"). Not
        # fixed here - fixing it means either having dispatcher.py
        # import and validate against this setting at startup, or
        # removing this setting and using dispatcher.supported_
        # extensions() as the sole source of truth wherever a list
        # of supported extensions is needed (e.g. the API layer).
    )

    # ------------------------------------------------------------------
    # Job system (app/jobs/) - queueing and the background worker pool
    # ------------------------------------------------------------------

    MAX_QUEUED_JOBS_PER_USER: int = Field(
        default=5,
        ge=1,
        # Renamed from MAX_UPLOADED_FILES_PER_SESSION (RFP-Analyzer-
        # era naming). This caps how many reviews can be queued behind
        # an in-progress one for a single user, enforced across ALL
        # conversations that user has open - not a per-session limit.
        # Actually enforced now (app/jobs/service.py's
        # submit_review_job()) - this setting existed unused for a
        # while before that wiring happened.
    )

    MAX_CONCURRENT_JOBS: int = Field(
        default=3,
        ge=1,
        # How many worker slots (app/jobs/worker.py) run concurrently
        # - a real, currently UNTUNED guess, same status as
        # image_extraction.py's own max_concurrent. Bounds how many
        # simultaneous review pipelines (each making real LLM calls)
        # can hit the shared GenAI service at once.
    )

    POLL_INTERVAL_SECONDS: int = Field(
        default=5,
        ge=1,
        # How often an idle worker slot re-checks for a new pending
        # job.
    )

    STALE_JOB_THRESHOLD_SECONDS: int = Field(
        default=900,
        ge=1,
        # A RUNNING job with no heartbeat this old gets requeued.
        # Heartbeat currently only updates at phase boundaries
        # (claimed/after-parse/after-review/completed), not
        # mid-batch within a single long LLM call - keep this
        # generous relative to real batch durations until that gets
        # finer-grained. Untuned - needs real data from a 100MB-scale
        # test run.
    )

    # ------------------------------------------------------------------
    # Microsoft Graph (RESERVED - future knowledge-sync source
    # registration, not yet built/used)
    # ------------------------------------------------------------------

    GRAPH_CLIENT_ID: str = ""

    GRAPH_CLIENT_SECRET: SecretStr = SecretStr("")

    GRAPH_TENANT_ID: str = ""

    SHAREPOINT_SITE_ID: str = ""

    SHAREPOINT_DRIVE_ID: str = ""

    SHAREPOINT_KNOWLEDGE_FOLDER: str = (
        "AI tool files/EditEdge"
    )

    # ------------------------------------------------------------------
    # Microsoft Teams (RESERVED - future channel integration, not yet
    # built; the REST API in app/api/v1/ is a dev/testing surface,
    # NOT how Teams will ultimately connect - see architecture doc
    # Section 1)
    # ------------------------------------------------------------------

    TEAMS_APP_ID: str = ""

    TEAMS_TENANT_ID: Optional[str] = None

    TEAMS_SESSION_STALE_DAYS: int = 3

    # ------------------------------------------------------------------
    # Chunking (RESERVED - future secondary vector-store Q&A feature,
    # not yet built/used by the review engine)
    # ------------------------------------------------------------------

    CHUNK_SIZE_TOKENS: int = Field(
        default=1500,
        ge=1,
        le=10000,
    )

    CHUNK_OVERLAP_TOKENS: int = Field(
        default=700,
        ge=0,
        le=10000,
    )

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------

    ENABLE_SWAGGER: bool = True

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    CORS_ORIGINS: List[str] = Field(
        default_factory=lambda: ["*"]
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _guard_auth(self) -> "Settings":
        # Fail fast: dev bypass must never be on in production.
        if self.IS_PRODUCTION and self.AUTH_DEV_BYPASS:
            raise ValueError(
                "AUTH_DEV_BYPASS must be False in production"
            )
        # FIXED REAL ISSUE: ENTRA_TENANT_ID used to be unconditionally
        # required (no default at all) - now only required when
        # AUTH_MODE="entra" is actually selected, so AUTH_MODE="header"
        # (the default) genuinely doesn't need any Entra config to
        # start up.
        if self.AUTH_MODE == "entra" and not self.ENTRA_TENANT_ID.strip():
            raise ValueError(
                "ENTRA_TENANT_ID is required when AUTH_MODE='entra'"
            )
        return self

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v):
        if v is None or v == "":
            return ["*"]

        if isinstance(v, str):
            s = v.strip()

            if s.startswith("["):
                try:
                    return json.loads(s)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"CORS_ORIGINS looks like JSON but failed "
                        f"to parse: {exc}"
                    ) from exc

            return [
                item.strip()
                for item in s.split(",")
                if item.strip()
            ]

        return v

    # ------------------------------------------------------------------
    # Source precedence
    #
    # secrets_dir > env > .env > defaults
    # ------------------------------------------------------------------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:

        return (
            init_settings,
            file_secret_settings,
            env_settings,
            dotenv_settings,
        )

    # ------------------------------------------------------------------
    # Derived flags
    # ------------------------------------------------------------------

    @computed_field  # type: ignore[misc]
    @property
    def IS_DEVELOPMENT(self) -> bool:
        return self.ENVIRONMENT == "development"

    @computed_field  # type: ignore[misc]
    @property
    def IS_TESTING(self) -> bool:
        return self.ENVIRONMENT == "testing"

    @computed_field  # type: ignore[misc]
    @property
    def IS_PRODUCTION(self) -> bool:
        return self.ENVIRONMENT == "production"

    # ------------------------------------------------------------------
    # Chunking sanity - overlap must be smaller than the chunk size
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_chunking(self) -> "Settings":
        if (
            self.CHUNK_OVERLAP_TOKENS
            >= self.CHUNK_SIZE_TOKENS
        ):
            raise ValueError(
                f"CHUNK_OVERLAP_TOKENS "
                f"({self.CHUNK_OVERLAP_TOKENS}) must be "
                f"less than CHUNK_SIZE_TOKENS "
                f"({self.CHUNK_SIZE_TOKENS})"
            )

        return self

    # ------------------------------------------------------------------
    # Production validation - fail fast on missing required values
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_required_in_production(
        self,
    ) -> "Settings":

        if not self.IS_PRODUCTION:
            return self

        api_key_ok = (
            self.GENAI_API_KEY is not None
            and self.GENAI_API_KEY.get_secret_value().strip()
            != ""
        )

        required = {
            "DB_CONNECTION_STRING": (
                self.DB_CONNECTION_STRING
                != "mongodb://localhost:27017"
            ),
            "GENAI_BASE_URL": bool(
                self.GENAI_BASE_URL
                and self.GENAI_BASE_URL.strip()
            ),
            "GENAI_API_KEY": api_key_ok,
        }

        missing = [
            name
            for name, ok in required.items()
            if not ok
        ]

        if missing:
            raise ValueError(
                "Missing required production settings: "
                f"{', '.join(missing)}"
            )

        if self.DEBUG:
            raise ValueError(
                "DEBUG must be False in production"
            )

        return self

    # ------------------------------------------------------------------
    # Safe representation for logging
    # (auto-masks all secrets)
    # ------------------------------------------------------------------

    def safe_dump(self) -> dict:
        """Return a dict representation safe to log
        (all secrets masked).
        """

        data = self.model_dump(mode="json")

        for name, field in self.model_fields.items():
            annotation = field.annotation

            is_secret = (
                annotation is SecretStr
                or annotation == Optional[SecretStr]
            )

            if is_secret and data.get(name) is not None:
                data[name] = "***"

        for url_key in ("DB_CONNECTION_STRING",):
            url = getattr(self, url_key, None)

            if url and "@" in url:
                try:
                    scheme, rest = url.split("://", 1)
                    _, host_part = rest.split("@", 1)

                    data[url_key] = (
                        f"{scheme}://***@{host_part}"
                    )
                except ValueError:
                    pass

        return data


# ----------------------------------------------------------------------
# Cached factory - import get_settings() everywhere.
# Avoids crashing on module import and plays nicely with tests / DI.
# ----------------------------------------------------------------------

@lru_cache
def get_settings() -> Settings:
    settings = Settings()

    logger.info(
        "EditEdge settings loaded for ENVIRONMENT=%s (DEBUG=%s)",
        settings.ENVIRONMENT,
        settings.DEBUG,
    )

    logger.debug(
        "Full settings: %s",
        settings.safe_dump(),
    )

    return settings


# Optional backward-compatible module-level singleton.
# Remove this line if you fully migrate to get_settings().
settings = get_settings()