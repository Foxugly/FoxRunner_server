"""Shared Ninja schemas for the FoxRunner Django backend.

These are deliberately project-wide rather than per-app: the FastAPI
backend exposed user-facing payload shapes from a single ``schemas``
module, and several apps need the same building blocks (e.g. ``UserOut``
is reused by admin endpoints in later phases).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema


class ErrorOut(Schema):
    """The {code, message, details} envelope returned by the global Ninja
    exception handler in ``foxrunner.exception_handlers``.

    Surfaced in OpenAPI by ``scripts/export_openapi.py``, which post-processes
    the Ninja-generated spec to attach this schema as the default response on
    every operation. Frontend consumers get strongly-typed errors out of the
    box without us needing ``responses={400: ErrorOut, 401: ErrorOut, ...}``
    on every single endpoint.
    """

    code: str
    message: str
    details: Any | None = None


class UserOut(Schema):
    id: UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    timezone_name: str
    date_joined: datetime


class UserPatchIn(Schema):
    timezone_name: str | None = None
    email: str | None = None
    password: str | None = None


class ForgotPasswordIn(Schema):
    email: str


class ResetPasswordIn(Schema):
    token: str
    password: str
