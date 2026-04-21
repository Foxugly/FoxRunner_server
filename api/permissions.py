from __future__ import annotations

from fastapi import HTTPException, status

from api.auth import User
from api.models import ScenarioRecord


def require_superuser(user: User) -> None:
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser requis.")


def require_user_scope(user_id: str, user: User) -> None:
    allowed_ids = {str(user.id), user.email}
    if user.is_superuser or user_id in allowed_ids:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acces utilisateur refuse.")


def require_scenario_owner(record: ScenarioRecord, user: User) -> None:
    if not user.is_superuser and record.owner_user_id != str(user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Seul le proprietaire peut modifier ce scenario.")


def scenario_role(record: ScenarioRecord, user: User) -> tuple[str, bool]:
    writable = user.is_superuser or record.owner_user_id == str(user.email)
    role = "superuser" if user.is_superuser else "owner" if writable else "reader"
    return role, writable
