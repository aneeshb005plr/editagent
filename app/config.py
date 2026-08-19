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

    GENAI_EMBEDDINGS_MODEL: str = (
        "azure.text-embedding-3-small"
    )

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
    # Microsoft Graph
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
    # Microsoft Teams (Managed Identity auth)
    # ------------------------------------------------------------------

    TEAMS_APP_ID: str = ""

    TEAMS_TENANT_ID: Optional[str] = None

    TEAMS_SESSION_STALE_DAYS: int = 3

    # ------------------------------------------------------------------
    # Chunking
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
    )

    MAX_QUEUED_JOBS_PER_USER: int = Field(
        default=5,
        ge=1,
        # Renamed from MAX_UPLOADED_FILES_PER_SESSION (RFP-Analyzer-
        # era naming). This caps how many reviews can be queued behind
        # an in-progress one for a single user, enforced across ALL
        # conversations that user has open - not a per-session limit.
    )

    AUTH_MODE: str = "header"          # or "entra" — new
    # (ENTRA_TENANT_ID, AUTH_DEV_BYPASS, IS_PRODUCTION already required by your real entra.py)
    POLL_INTERVAL_SECONDS: int = 5
    STALE_JOB_THRESHOLD_SECONDS: int = 900
    MAX_CONCURRENT_JOBS: int = 3

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    ENTRA_TENANT_ID: str

    AUTH_DEV_BYPASS: bool = False
    # never on in prod

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