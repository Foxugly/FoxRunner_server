"""Permission helpers shared across apps.

``require_superuser`` raises ``HttpError(403)`` when the user is not a
superuser. ``resolve_user`` accepts a UUID or an email (the dual-stack
identifier shape supported during phase 4). ``require_user_scope`` and
``require_self_or_superuser`` mirror ``api/permissions.py``.

After the UUID normalization (phase 5), ``require_scenario_owner``
compares directly on ``scenario.owner_id == user.id`` — the
``_owner_candidates`` fallback from the FastAPI implementation is gone.
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
    """Authorize when actor is superuser or matches user_id (UUID or email)."""
    allowed = {str(actor.id), actor.email}
    if actor.is_superuser or user_id in allowed:
        return
    raise HttpError(403, "Acces utilisateur refuse.")


def require_self_or_superuser(actor: User, target: User) -> None:
    if actor.is_superuser or actor.id == target.id:
        return
    raise HttpError(403, "Acces interdit a cet utilisateur.")
