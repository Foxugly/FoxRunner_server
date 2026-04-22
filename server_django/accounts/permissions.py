"""Permission helpers shared across apps.

``require_superuser`` raises ``HttpError(403)`` when the user is not a
superuser. After the UUID normalization (phase 5), ``require_scenario_owner``
compares directly on ``scenario.owner_id == user.id`` — the
``_owner_candidates`` fallback from the FastAPI implementation is gone.
"""

from __future__ import annotations

from ninja.errors import HttpError


def require_superuser(user) -> None:
    if not getattr(user, "is_superuser", False):
        raise HttpError(403, "Superuser requis.")
