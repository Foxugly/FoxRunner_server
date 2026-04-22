"""Accounts domain services.

Kept in a dedicated module so Ninja handlers stay thin. Populated during
migration phase 2.
"""

from __future__ import annotations

import contextlib
import uuid

from django.core.exceptions import ValidationError

from accounts.models import User


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
