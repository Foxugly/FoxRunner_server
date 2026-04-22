"""Global exception handlers for the Ninja API.

Response contract (matches the FastAPI implementation):

    {"code": str, "message": str, "details": Any | None}
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied
from django.http import Http404
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from app.redaction import redact_text


def _error_response(api: NinjaAPI, request, status: int, code: str, message: str, details: Any = None):
    return api.create_response(
        request,
        {"code": code, "message": message, "details": details},
        status=status,
    )


def install_handlers(api: NinjaAPI) -> None:
    @api.exception_handler(HttpError)
    def _http_error(request, exc: HttpError):
        return _error_response(api, request, exc.status_code, _code_for_status(exc.status_code), redact_text(str(exc.message)))

    @api.exception_handler(ValidationError)
    def _validation(request, exc: ValidationError):
        return _error_response(api, request, 422, "validation_error", "Payload ou parametres invalides.", details=exc.errors)

    @api.exception_handler(AuthenticationError)
    def _auth(request, exc: AuthenticationError):
        return _error_response(api, request, 401, "unauthorized", "Authentification requise.")

    @api.exception_handler(PermissionDenied)
    def _forbidden(request, exc: PermissionDenied):
        return _error_response(api, request, 403, "forbidden", redact_text(str(exc) or "Acces refuse."))

    @api.exception_handler(Http404)
    def _not_found(request, exc: Http404):
        return _error_response(api, request, 404, "not_found", redact_text(str(exc) or "Ressource introuvable."))

    @api.exception_handler(Exception)
    def _unhandled(request, exc: Exception):
        from django.conf import settings

        production = str(getattr(settings, "APP_ENV", "")).lower() in {"production", "prod"} or not getattr(settings, "DEBUG", False)
        message = "Erreur interne." if production else redact_text(str(exc))
        return _error_response(api, request, 500, "internal_error", message)


def _code_for_status(status_code: int) -> str:
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
    }
    if status_code >= 500:
        return "internal_error"
    return mapping.get(status_code, "http_error")
