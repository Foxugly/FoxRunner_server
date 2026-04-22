"""FoxRunner Django project."""

from __future__ import annotations

# Re-export celery_app so `celery -A foxrunner worker` resolves.
from .celery import celery_app

__all__ = ["celery_app"]
