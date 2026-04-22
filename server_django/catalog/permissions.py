"""Object-level permission helpers for catalog resources.

Owner identity is stored as either the UUID or the email depending on the
write path (JSON seed vs API create). Until Phase 5 normalizes everything
to UUID strings, ownership checks must accept both.
"""

from __future__ import annotations

from accounts.models import User
from ninja.errors import HttpError

from catalog.models import Scenario


def _is_scenario_owner(scenario: Scenario, user: User) -> bool:
    return scenario.owner_user_id in {str(user.id), user.email}


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
