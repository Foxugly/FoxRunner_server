"""Top-level URL configuration.

All API routes live under ``/api/v1/``:
    - ``/api/v1/auth/*``      — djoser (register, password reset)
    - ``/api/v1/auth/jwt/*``  — djoser JWT create/refresh + Ninja wrappers
    - ``/api/v1/*``           — Ninja routers for the rest of the API

``/admin/`` exposes the Django admin (session-auth, CSRF-protected).
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

from foxrunner.api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("djoser.urls")),
    path("api/v1/auth/jwt/", include("djoser.urls.jwt")),
    path("api/v1/", api.urls),
]
