# app/auth/entra.py

import logging
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Request

from app.config import settings
from app.utils.error_handlers import APIError


logger = logging.getLogger("app.auth")


class AuthError(APIError):
    def __init__(self, message="Authentication error", payload=None):
        super().__init__(message, 401, payload)


class AuthConfigError(APIError):
    def __init__(self, message="Authentication service unavailable", payload=None):
        super().__init__(message, 503, payload)


class UserContext:
    def __init__(self, token_claims):
        self._claims = token_claims or {}

    @property
    def user_id(self):
        return self._claims.get("oid") or self._claims.get("sub")  # stable oid preferred

    @property
    def guid(self):
        return self._claims.get("uid") or self._claims.get("oid")

    @property
    def email(self):
        return (
            self._claims.get("preferred_username")
            or self._claims.get("email")
            or self._claims.get("preferredMail")
        )

    @property
    def name(self):
        return self._claims.get("name")

    @property
    def first_name(self):
        return self._claims.get("given_name")

    @property
    def last_name(self):
        return self._claims.get("family_name")

    @property
    def tenant_id(self):
        return self._claims.get("tid")

    def get_claim(self, name):
        return self._claims.get(name)

    def __str__(self):
        return f"UserContext(user_id={self.user_id}, name={self.name}, email={self.email})"


_ISSUER = f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/v2.0"


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    # Caches keys WITH rotation handling — safe to cache the client.
    return PyJWKClient(
        f"https://login.microsoftonline.com/{settings.ENTRA_TENANT_ID}/discovery/v2.0/keys"
    )


def get_token_from_header(request: Request):
    parts = request.headers.get("Authorization", "").split()

    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None

    return parts[1]


def decode_token(token: str) -> dict:
    if not token:
        raise AuthError("No token provided")

    if settings.AUTH_DEV_BYPASS and not settings.IS_PRODUCTION:
        logger.warning("AUTH_DEV_BYPASS active — signature NOT verified (dev only)")
        return jwt.decode(token, options={"verify_signature": False})

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientError as e:
        logger.error("JWKS resolution failed: %s", e)
        raise AuthConfigError("Unable to retrieve signing keys") from e

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=_ISSUER,
            # No audience allow-list: we authenticate, we don't authorize by app.
            options={
                "require": ["exp", "iat", "nbf", "iss"],
                "verify_signature": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": False,  # deliberate: aud varies per client, we don't gate on it
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("Token has expired") from e
    except jwt.InvalidIssuerError as e:
        raise AuthError("Invalid token issuer") from e
    except jwt.InvalidTokenError as e:
        logger.warning("Token validation failed: %s", e)
        raise AuthError("Invalid token") from e

    # Tenant guard replaces the client-id gate: only OUR tenant's users.
    if claims.get("tid") != settings.ENTRA_TENANT_ID:
        raise AuthError("Untrusted tenant")

    return claims


def get_current_user(request: Request) -> UserContext:
    claims = decode_token(get_token_from_header(request))
    user = UserContext(claims)
    request.state.user = user
    return user


def require_auth(request: Request) -> UserContext:
    return get_current_user(request)