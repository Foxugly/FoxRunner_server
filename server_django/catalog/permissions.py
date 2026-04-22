"""Object-level permission helpers for catalog resources."""

from __future__ import annotations

from ninja.errors import HttpError


def require_scenario_owner(scenario, user) -> None:
    # After the UUID normalization (phase 5) this comparison is final —
    # no email fallback.
    if getattr(user, "is_superuser", False):
        return
    if getattr(scenario, "owner_id", None) == user.id:
        return
    raise HttpError(403, "Seul le proprietaire peut modifier ce scenario.")
