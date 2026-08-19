"""
app/auth/dependencies.py

get_current_user_id() as a shared dependency, supporting TWO real
modes via settings.AUTH_MODE:

  AUTH_MODE = "header"  - reads X-User-Id directly, no cryptographic
                           validation. Matches the confirmed reality
                           that Entra auth was NOT implemented in the
                           first app this pattern is modeled on -
                           this is a legitimate mode for that
                           situation, not just a testing shortcut.

  AUTH_MODE = "entra"   - delegates to the REAL app.auth.entra module
                           (Entra ID JWT validation against the real
                           JWKS endpoint via PyJWT). NOT a separate
                           reimplementation - an earlier version of
                           this file had its own independent JWKS/
                           audience-check logic, written before this
                           agent had seen the real entra.py. That was
                           removed once the real module was available:
                           running two separate token-validation
                           implementations side by side is a real,
                           avoidable risk (one gets updated for a
                           signing-key rotation policy or a claims
                           change, the other silently doesn't). Note
                           the real entra.py deliberately does NOT
                           validate the audience claim ("aud varies
                           per client, we don't gate on it" - its own
                           comment) and tenant-gates instead - that is
                           entra.py's considered design choice, not
                           reconciled or overridden here.

REQUIRES ONE CONFIG SETTING beyond what entra.py itself already needs
(ENTRA_TENANT_ID, AUTH_DEV_BYPASS, IS_PRODUCTION - all already
required by app/auth/entra.py, confirmed from its own source):

    AUTH_MODE: str = "header"
    # "header" or "entra" - which mode is active for THIS API surface
    # specifically. Defaults to "header" to match the confirmed
    # current reality (no Entra integration wired up for this app
    # yet) - flip to "entra" once that's genuinely configured for a
    # given environment. Independent of entra.py's own AUTH_DEV_BYPASS,
    # which controls something different (whether Entra token
    # SIGNATURES get verified once AUTH_MODE="entra" is already
    # selected) - these are two separate, complementary switches, not
    # duplicates of each other.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.auth.entra import get_current_user as get_entra_user
from app.config import settings
from app.utils.error_handlers import APIError

logger = logging.getLogger("app.auth.dependencies")


def _get_entra_user_id(request: Request) -> str:
    try:
        user = get_entra_user(request)
    except APIError as e:
        # entra.py's AuthError/AuthConfigError already carry the
        # correct status_code (401/503) and message - converted here
        # rather than left to propagate, so this dependency always
        # produces a sane HTTP response regardless of whether a
        # global APIError exception handler is registered elsewhere
        # in the app (not confirmed either way).
        raise HTTPException(status_code=e.status_code, detail=e.message)

    if not user.user_id:
        raise HTTPException(status_code=401, detail="Token missing user identity claim")
    return user.user_id


def _get_header_user_id(request: Request) -> str:
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    return user_id


def get_current_user_id(request: Request) -> str:
    """The actual FastAPI dependency routes should use - branches on
    settings.AUTH_MODE, real logic on both sides (not one real path
    and one stub)."""

    if settings.AUTH_MODE == "entra":
        return _get_entra_user_id(request)
    elif settings.AUTH_MODE == "header":
        return _get_header_user_id(request)
    else:
        raise RuntimeError(
            f"Unknown AUTH_MODE={settings.AUTH_MODE!r} - expected 'header' or 'entra'"
        )