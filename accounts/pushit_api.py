"""Ninja router for per-user PushIT targets.

CRUD for the PushIT applications a user can notify. Every query is scoped
to ``request.auth`` -- a user only ever sees, edits, and deletes their own
targets (the config is per-user, never shared). The token is the owner's
own secret and is returned to its owner so the frontend editor can show
and modify it.

    GET    /api/v1/pushit-targets
    POST   /api/v1/pushit-targets
    PATCH  /api/v1/pushit-targets/{target_id}
    DELETE /api/v1/pushit-targets/{target_id}
    POST   /api/v1/pushit-targets/{target_id}/test   (send a test notification)
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from ninja import Router
from ninja.errors import HttpError

from accounts.models import PushItTarget, User
from accounts.services import _target_to_config
from app.logger import Logger
from app.notifier import Notifier
from foxrunner.serializers import (
    PushItTargetIn,
    PushItTargetOut,
    PushItTargetPatchIn,
    PushItTestOut,
)
from ops.services import write_audit

router = Router(tags=["pushit"])


def _serialize(target: PushItTarget) -> dict:
    return {
        "id": target.id,
        "name": target.name,
        "app_token": target.app_token,
        "base_url": target.base_url,
        "title": target.title,
        "is_default": target.is_default,
    }


def _get_owned(target_id: int, user: User) -> PushItTarget:
    try:
        return PushItTarget.objects.get(id=target_id, owner=user)
    except PushItTarget.DoesNotExist:
        raise HttpError(404, "Application PushIT introuvable.") from None


@router.get("/pushit-targets", response=list[PushItTargetOut])
def list_pushit_targets(request):
    return [_serialize(target) for target in PushItTarget.objects.filter(owner=request.auth)]


@router.post("/pushit-targets", response={201: PushItTargetOut})
def create_pushit_target(request, payload: PushItTargetIn):
    user: User = request.auth
    if not payload.name.strip():
        raise HttpError(422, "Le nom est requis.")
    if not payload.app_token.strip():
        raise HttpError(422, "Le token est requis.")
    try:
        with transaction.atomic():
            target = PushItTarget.objects.create(
                owner=user,
                name=payload.name.strip(),
                app_token=payload.app_token.strip(),
                base_url=payload.base_url.strip() or "https://pushit-api.foxugly.com/api/v1",
                title=payload.title.strip() or "FoxRunner",
                is_default=payload.is_default,
            )
    except IntegrityError:
        raise HttpError(409, "Une application PushIT porte deja ce nom.") from None
    write_audit(actor=user, action="pushit_target.create", target_type="pushit_target", target_id=str(target.id), after=_serialize(target))
    return 201, _serialize(target)


@router.patch("/pushit-targets/{target_id}", response=PushItTargetOut)
def update_pushit_target(request, target_id: int, payload: PushItTargetPatchIn):
    user: User = request.auth
    target = _get_owned(target_id, user)
    if payload.name is not None:
        if not payload.name.strip():
            raise HttpError(422, "Le nom est requis.")
        target.name = payload.name.strip()
    if payload.app_token is not None:
        if not payload.app_token.strip():
            raise HttpError(422, "Le token est requis.")
        target.app_token = payload.app_token.strip()
    if payload.base_url is not None:
        target.base_url = payload.base_url.strip() or "https://pushit-api.foxugly.com/api/v1"
    if payload.title is not None:
        target.title = payload.title.strip() or "FoxRunner"
    if payload.is_default is not None:
        target.is_default = payload.is_default
    try:
        with transaction.atomic():
            target.save()
    except IntegrityError:
        raise HttpError(409, "Une application PushIT porte deja ce nom.") from None
    write_audit(actor=user, action="pushit_target.update", target_type="pushit_target", target_id=str(target.id), after=_serialize(target))
    return _serialize(target)


@router.delete("/pushit-targets/{target_id}", response={204: None})
def delete_pushit_target(request, target_id: int):
    user: User = request.auth
    target = _get_owned(target_id, user)
    before = _serialize(target)
    target.delete()
    write_audit(actor=user, action="pushit_target.delete", target_type="pushit_target", target_id=str(target_id), before=before)
    return 204, None


@router.post("/pushit-targets/{target_id}/test", response=PushItTestOut)
def test_pushit_target(request, target_id: int):
    """Send a one-off test notification through this target.

    Lets the user confirm the token/base_url from the UI without waiting
    for a real scheduler alert. Returns ``{sent: bool}`` -- a False means
    PushIT rejected the call (bad token, network) so the UI can flag it.
    """
    user: User = request.auth
    target = _get_owned(target_id, user)
    notifier = Notifier(None, Logger(debug_enabled=False), pushit=_target_to_config(target))
    sent = notifier.send(f"Test FoxRunner -> {target.name}")
    return {"sent": sent}
