"""Object-level permission helpers for catalog resources.

After phase 5 (UUID normalization + FK promotion), ownership is canonical
UUID-only. ``Scenario.owner`` is a ``ForeignKey(User)``, so ownership
checks compare directly on ``scenario.owner_id == user.id`` -- the
dual-stack UUID-or-email fallback that lived here pre-phase-5 is gone.
"""

from __future__ import annotations

from accounts.models import User
from ninja.errors import HttpError

from catalog.models import Scenario


def _is_scenario_owner(scenario: Scenario, user: User) -> bool:
    return scenario.owner_id == user.id


def require_scenario_owner(scenario: Scenario, user: User) -> None:
    if user.is_superuser or _is_scenario_owner(scenario, user):
        return
    raise HttpError(403, "Seul le proprietaire peut modifier ce scenario.")


def scenario_role(scenario: Scenario, user: User) -> tuple[str, bool]:
    """Returns (role, writable) where role in {superuser, owner, reader}."""
    writable = user.is_superuser or _is_scenario_owner(scenario, user)
    if user.is_superuser:
        return "superuser", True
    return ("owner", True) if writable else ("reader", False)
