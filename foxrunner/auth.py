"""JWT authentication adapter for Django Ninja.

Reuses simple-jwt (already required by djoser) so the Bearer token minted
at ``/api/v1/auth/jwt/create`` is accepted by every Ninja endpoint.
"""

from __future__ import annotations

from typing import Any

from ninja.security import HttpBearer
from rest_framework_simplejwt.authentication import JWTAuthentication


class JWTAuth(HttpBearer):
    """Decode the JWT with simple-jwt and expose ``request.auth`` as the user."""

    def authenticate(self, request, token: str) -> Any | None:  # noqa: D401
        authenticator = JWTAuthentication()
        try:
            validated = authenticator.get_validated_token(token)
            user = authenticator.get_user(validated)
        except Exception:
            return None
        request.user = user  # convenience so Ninja handlers can use request.user
        return user


class OptionalJWTAuth(JWTAuth):
    """Non-enforcing variant for routes that accept anonymous access (webhooks)."""

    def __call__(self, request):  # type: ignore[override]
        try:
            return super().__call__(request)
        except Exception:
            return None
