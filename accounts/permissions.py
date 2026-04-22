"""Permission helpers shared across apps.

``require_superuser`` raises ``HttpError(403)`` when the user is not a
superuser. ``resolve_user`` accepts a UUID or an email -- the path
parameter shape used by the user-scoped routes (``/users/{user_id}/...``)
where the frontend may pass either form. ``require_user_scope`` and
``require_self_or_superuser`` mirror ``api/permissions.py``.

After phase 5 the storage shape is canonical UUID, but ``user_id`` URL
path params still accept the email alias for backward compatibility --
the resolver normalizes both to a User row before the comparison.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from ninja.errors import HttpError

from accounts.models import User


def require_superuser(user) -> None:
    if not getattr(user, "is_superuser", False):
        raise HttpError(403, "Superuser requis.")


def resolve_user(user_id_str: str) -> User:
    """Accept a UUID or an email, return the User. Raises 404 if neither matches."""
    try:
        return User.objects.get(id=user_id_str)
    except (User.DoesNotExist, ValueError, ValidationError):
        try:
            return User.objects.get(email=user_id_str)
        except User.DoesNotExist:
            raise HttpError(404, "Utilisateur introuvable.") from None


def require_user_scope(user_id: str, actor: User) -> None:
    """Authorize when actor is superuser or matches user_id (UUID or email).

    The frontend still passes either form on the path, so we keep
    accepting both and resolve internally to compare User objects.
    """
    if actor.is_superuser:
        return
    try:
        target = resolve_user(user_id)
    except HttpError:
        raise HttpError(403, "Acces utilisateur refuse.") from None
    if target.id == actor.id:
        return
    raise HttpError(403, "Acces utilisateur refuse.")


def require_self_or_superuser(actor: User, target: User) -> None:
    if actor.is_superuser or actor.id == target.id:
        return
    raise HttpError(403, "Acces interdit a cet utilisateur.")
