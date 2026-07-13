"""Ninja router for account-scoped endpoints.

Wraps djoser / simple-jwt to preserve the FastAPI contract used by the
Angular client:

    POST /api/v1/auth/register          (inactive user + activation email, silent 202)
    POST /api/v1/auth/activate          (TimestampSigner token -> active + verified)
    POST /api/v1/auth/jwt/login         (form data -> {access_token, token_type})
    POST /api/v1/auth/jwt/logout        (no-op for bearer transport)
    POST /api/v1/auth/forgot-password   (silent for unknown emails)
    POST /api/v1/auth/reset-password    (TimestampSigner token, single-use by TTL)
    GET  /api/v1/users/me
    PATCH /api/v1/users/me

djoser still owns ``/api/v1/auth/jwt/create|refresh|verify`` for the
JSON-based JWT flows; registration is now handled here (Ninja) rather than
by djoser so new accounts start inactive and go through email activation.
"""

from __future__ import annotations

from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from django.contrib.auth import authenticate
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from ninja import Router
from ninja.errors import HttpError
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.activation import make_activation_token, parse_activation_token
from accounts.magic_link import make_magic_link_token, parse_magic_link_token
from accounts.models import User
from foxrunner.serializers import (
    ActivateIn,
    ForgotPasswordIn,
    MagicLinkExchangeIn,
    MagicLinkRequestIn,
    RegisterIn,
    ResetPasswordIn,
    UserOut,
    UserPatchIn,
)

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
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "token_type": "bearer",
    }


@router.post("/auth/jwt/logout", auth=None, summary="Logout (revokes the refresh token)")
def jwt_logout(request) -> dict[str, str]:
    """Blacklist the supplied refresh token (best-effort). Tolerant if the body
    has no / an invalid refresh so a stale client can still 'log out'."""
    import contextlib
    import json

    from rest_framework_simplejwt.exceptions import TokenError

    raw = request.body.decode("utf-8") if request.body else ""
    token = ""
    if raw:
        with contextlib.suppress(json.JSONDecodeError):
            token = (json.loads(raw) or {}).get("refresh", "")
    if token:
        with contextlib.suppress(TokenError):
            RefreshToken(token).blacklist()
    return {"status": "ok"}


@router.post("/auth/register", auth=None, response={202: dict}, summary="Self-service registration (email activation)")
def register(request, payload: RegisterIn):
    """Create a pending account and email an activation link.

    Anti-enumeration: always returns ``202 {status: queued}`` regardless of
    whether the email is already taken, so the endpoint never reveals which
    addresses have accounts. The new user is created *inactive* and
    *unverified*; it cannot log in until the activation link is followed.

    - unknown email  -> create inactive user + send activation link
    - pending email  -> resend the activation link (password untouched)
    - active email   -> silent no-op (the owner uses forgot-password instead)

    Password strength is validated first (independent of email existence, so
    it leaks nothing); a weak password returns 422.
    """
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError

    email = User.objects.normalize_email(payload.email)
    try:
        validate_password(payload.password)
    except DjangoValidationError:
        raise HttpError(422, "Mot de passe invalide.") from None

    from app.mail import send_activation_email

    existing = User.objects.filter(email__iexact=email).first()
    if existing is None:
        user = User.objects.create_user(email=email, password=payload.password, is_active=False, is_verified=False)
        send_activation_email(user.email, make_activation_token(user.id))
    elif not existing.is_active:
        # Pending account (never activated): resend the link, leave the
        # stored password alone so a stranger can't silently overwrite it.
        send_activation_email(existing.email, make_activation_token(existing.id))
    # else: already-active account -> stay silent (no enumeration signal).

    return 202, {"status": "queued"}


@router.post("/auth/activate", auth=None, summary="Activate a pending account")
def activate(request, payload: ActivateIn) -> dict[str, str]:
    """Validate the activation token and flip the account live.

    410 if the link has expired, 400 if it is invalid / unknown. Activating
    an already-active account is idempotent (re-affirms the flags)."""
    try:
        user_id = parse_activation_token(payload.token)
    except SignatureExpired:
        raise HttpError(410, "Lien d'activation expire.") from None
    except BadSignature:
        raise HttpError(400, "Lien d'activation invalide.") from None

    try:
        user = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError):
        raise HttpError(400, "Lien d'activation invalide.") from None

    if not (user.is_active and user.is_verified):
        user.is_active = True
        user.is_verified = True
        user.save(update_fields=["is_active", "is_verified"])
    return {"status": "ok"}


@router.post("/auth/forgot-password", auth=None, response={202: dict})
def forgot_password(request, payload: ForgotPasswordIn):
    """Silent for unknown emails (no enumeration)."""
    user = User.objects.filter(email=payload.email).first()
    if user is not None:
        token = TimestampSigner(salt=PASSWORD_RESET_SALT).sign(str(user.id))
        from app.mail import send_password_reset_email

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


@router.post("/auth/magic-link/request", auth=None, response={202: dict})
def magic_link_request(request, payload: MagicLinkRequestIn):
    """Silent for unknown / ineligible emails (no enumeration)."""
    user = User.objects.filter(email__iexact=payload.email, is_active=True, is_verified=True).first()
    if user is not None:
        token = make_magic_link_token(user.id)
        from app.mail import send_magic_link_email

        send_magic_link_email(user.email, token)
    return 202, {"status": "queued"}


@router.post("/auth/magic-link/exchange", auth=None)
def magic_link_exchange(request, payload: MagicLinkExchangeIn) -> dict[str, str]:
    """Exchange a magic-link token for a JWT. 410 expired, 400 invalid."""
    try:
        user_id = parse_magic_link_token(payload.token)
    except SignatureExpired:
        raise HttpError(410, "Lien expire.") from None
    except BadSignature:
        raise HttpError(400, "Lien invalide.") from None

    try:
        user = User.objects.get(id=user_id)
    except (User.DoesNotExist, ValueError):
        raise HttpError(400, "Lien invalide.") from None

    if not (user.is_active and user.is_verified):
        raise HttpError(400, "Lien invalide.") from None

    refresh = RefreshToken.for_user(user)
    return {
        "access_token": str(refresh.access_token),
        "refresh_token": str(refresh),
        "token_type": "bearer",
    }


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
