"""Ninja router for account-scoped endpoints.

Wraps djoser / simple-jwt to preserve the FastAPI contract used by the
Angular client:

    POST /api/v1/auth/jwt/login         (form data -> {access_token, token_type})
    POST /api/v1/auth/jwt/logout        (no-op for bearer transport)
    POST /api/v1/auth/forgot-password   (silent for unknown emails)
    POST /api/v1/auth/reset-password    (TimestampSigner token, single-use by TTL)
    GET  /api/v1/users/me
    PATCH /api/v1/users/me

djoser still owns ``/api/v1/auth/users/`` (register) and
``/api/v1/auth/jwt/create|refresh|verify`` for the JSON-based flows.
"""

from __future__ import annotations

from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from django.contrib.auth import authenticate
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from foxrunner.serializers import ForgotPasswordIn, ResetPasswordIn, UserOut, UserPatchIn
from ninja import Router
from ninja.errors import HttpError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User

router = Router(tags=["auth"])

PASSWORD_RESET_SALT = "accounts.password_reset"
PASSWORD_RESET_MAX_AGE_SECONDS = 3600


@router.post("/auth/jwt/login", auth=None, summary="Login (form data)")
def jwt_login(request) -> dict[str, str]:
    """Form-urlencoded login matching the FastAPI ``OAuth2PasswordBearer`` flow.

    Reads ``username`` (or ``email`` as a courtesy alias) and ``password``
    from the body, returns ``{access_token, token_type: "bearer"}``.
    """
    raw = request.body.decode("utf-8") if request.body else ""
    form = parse_qs(raw)
    username = (form.get("username") or form.get("email") or [""])[0]
    password = (form.get("password") or [""])[0]
    if not username or not password:
        raise HttpError(400, "Identifiants invalides.")
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        raise HttpError(401, "Identifiants invalides.")
    refresh = RefreshToken.for_user(user)
    return {"access_token": str(refresh.access_token), "token_type": "bearer"}


@router.post("/auth/jwt/logout", auth=None, summary="Logout (no-op for bearer transport)")
def jwt_logout(request) -> dict[str, str]:
    """Accepted for client compatibility. JWT lifetime is bounded server-side."""
    return {"status": "ok"}


@router.post("/auth/forgot-password", auth=None, response={202: dict})
def forgot_password(request, payload: ForgotPasswordIn):
    """Silent for unknown emails (no enumeration)."""
    user = User.objects.filter(email=payload.email).first()
    if user is not None:
        token = TimestampSigner(salt=PASSWORD_RESET_SALT).sign(str(user.id))
        from api.mail import send_password_reset_email  # reused until phase 13

        send_password_reset_email(user.email, token)
    return 202, {"status": "queued"}


@router.post("/auth/reset-password", auth=None)
def reset_password(request, payload: ResetPasswordIn) -> dict[str, str]:
    """Validate the TimestampSigner token and apply the new password.

    The token embeds the user_id, so the frontend payload is just
    ``{token, password}`` -- no ``user_id`` field. Single-use is enforced
    by the 3600s ``max_age`` on ``unsign`` (matches FastAPI behaviour).
    """
    signer = TimestampSigner(salt=PASSWORD_RESET_SALT)
    try:
        user_id = signer.unsign(payload.token, max_age=PASSWORD_RESET_MAX_AGE_SECONDS)
    except SignatureExpired:
        raise HttpError(400, "Token expire.") from None
    except BadSignature:
        raise HttpError(400, "Token invalide.") from None

    try:
        user = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError):
        raise HttpError(400, "Token invalide.") from None

    user.set_password(payload.password)
    user.save(update_fields=["password"])
    return {"status": "ok"}


@router.get("/users/me", response=UserOut)
def users_me(request) -> User:
    return request.auth


@router.patch("/users/me", response=UserOut)
def users_me_patch(request, payload: UserPatchIn) -> User:
    user: User = request.auth
    if payload.timezone_name is not None:
        try:
            ZoneInfo(payload.timezone_name)
        except Exception as exc:
            raise HttpError(422, "Timezone IANA invalide.") from exc
        user.timezone_name = payload.timezone_name
    if payload.email is not None:
        user.email = payload.email
    if payload.password is not None:
        user.set_password(payload.password)
    user.save()
    return user
