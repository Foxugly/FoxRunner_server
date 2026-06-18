"""Accounts domain services.

Kept in a dedicated module so Ninja handlers stay thin. Populated during
migration phase 2.
"""

from __future__ import annotations

import contextlib
import uuid

from django.core.exceptions import ValidationError

from accounts.models import PushItTarget, User
from app.config import PushItConfig


def timezone_for_user(user_id_str: str, current_user: User) -> str:
    """Return the IANA timezone of the user identified by ``user_id_str``.

    Mirrors ``api/services/users.py::timezone_for_user``: when the
    identifier matches the actor (UUID or email) the actor's timezone is
    returned without an extra DB hit; otherwise the User row is looked
    up by UUID-or-email and its ``timezone_name`` is returned.

    Falls back to ``current_user.timezone_name`` when the target user
    cannot be resolved -- the FastAPI version does the same so the
    caller never has to handle a None/empty timezone.
    """
    if user_id_str in {str(current_user.id), current_user.email}:
        return current_user.timezone_name
    target: User | None = None
    with contextlib.suppress(ValueError, ValidationError, User.DoesNotExist):
        target = User.objects.get(id=uuid.UUID(user_id_str))
    if target is None:
        with contextlib.suppress(User.DoesNotExist):
            target = User.objects.get(email=user_id_str)
    return target.timezone_name if target is not None else current_user.timezone_name


# --------------------------------------------------------------------------
# PushIT targets (per-user notification apps). The CLI scheduler and the
# server both resolve a scenario owner's config through
# ``load_pushit_config_for_owner`` -- the DB is the single source of truth.
# --------------------------------------------------------------------------


def _target_to_config(target: PushItTarget) -> PushItConfig:
    return PushItConfig(
        app_token=target.app_token,
        base_url=target.base_url or "https://pushit-api.foxugly.com/api/v1",
        title=target.title or "FoxRunner",
    )


def load_pushit_config_for_owner(owner_id) -> PushItConfig | None:
    """Return the default PushIT config for ``owner_id`` from the DB, or None.

    Picks the owner's ``is_default`` target first, else the alphabetically
    first one. Returns None when the owner has no target, ``owner_id`` is
    falsy, or the DB is simply unavailable (so the CLI engine still works
    in pure-unit-test contexts with no database configured).
    """
    if not owner_id:
        return None
    try:
        target = PushItTarget.objects.filter(owner_id=owner_id).order_by("-is_default", "name").first()
    except Exception:
        return None
    return _target_to_config(target) if target is not None else None
