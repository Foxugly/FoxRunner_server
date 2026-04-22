"""Normalize ``AuditEntry.actor_user_id`` from email to UUID strings.

Mirror of ``migrations/versions/20260422_0012_normalize_owner_user_id.py``
for the Django ORM (audit_log portion only -- catalog has its own data
migration).

Idempotent + one-way (see catalog/0002_normalize_owner_user_id docstring).

``Job.user_id`` and ``IdempotencyKey.user_id`` are intentionally left
alone here: they only ever held UUID strings (jobs are always
API-created; idempotency keys are an internal cache scoped to the
caller's UUID).
"""

from __future__ import annotations

from django.db import migrations


def normalize_actor_user_id(apps, schema_editor) -> None:
    User = apps.get_model("accounts", "User")
    AuditEntry = apps.get_model("ops", "AuditEntry")

    for user in User.objects.all():
        if not user.email:
            continue
        AuditEntry.objects.filter(actor_user_id=user.email).update(actor_user_id=str(user.id))


def noop(apps, schema_editor) -> None:
    """One-way migration -- see module docstring."""


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(normalize_actor_user_id, noop),
    ]
