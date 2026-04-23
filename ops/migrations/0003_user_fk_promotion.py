"""Promote ops ``*_user_id`` columns.

Three changes here, all on ops tables, all preserving the original
column name via ``db_column=``:

  * ``Job.user_id``        -> ``Job.user``: ``ForeignKey(User, on_delete=PROTECT)``.
    Jobs are created via the API and the column has only ever held UUID
    strings; no data normalization required, just the type flip.
  * ``AuditEntry.actor_user_id`` -> ``AuditEntry.actor``:
    ``ForeignKey(User, null=True, on_delete=SET_NULL)``. Nullability
    flips from NOT NULL to NULL here -- the FK promotion intentionally
    relaxes the column so deleting a User does not destroy the audit
    history (the row stays with ``actor=NULL``). Data was normalized in
    ``0002_normalize_actor_user_id``.
  * ``IdempotencyKey.user_id`` -> ``UUIDField`` (no FK promotion).
    The column is an internal cache key; we do NOT want to cascade or
    block User deletion through it. Type-flip only.

Indexes on ``jobs.user_id`` (single-column + composite
``ix_jobs_user_status``) auto-rebuild against the new column type.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def _cleanup_orphan_user_refs(apps, schema_editor) -> None:
    """Make ops rows safe for the FK constraints below.

    - Job.user_id is about to become FK(User, on_delete=PROTECT). Reassign
      orphan jobs to the sentinel User (created by catalog/0003 if not
      already there) so the constraint applies.
    - AuditEntry.actor_user_id is about to become FK(User, null=True,
      on_delete=SET_NULL). Orphan rows are NULLed in place — preserves the
      audit history without a sentinel reference.
    - IdempotencyKey.user_id is about to become UUIDField (no FK). Delete
      rows whose value isn't a parseable UUID; the table is an internal
      24h cache so the loss is acceptable.
    """
    import uuid

    User = apps.get_model("accounts", "User")
    Job = apps.get_model("ops", "Job")
    AuditEntry = apps.get_model("ops", "AuditEntry")
    IdempotencyKey = apps.get_model("ops", "IdempotencyKey")

    # SQLite UUIDField storage is 32-char hex without dashes. Normalise the
    # CharField values likewise so the FK check passes after AlterField.
    valid_ids = {uid.hex for uid in User.objects.values_list("id", flat=True)}

    def _norm(value: str) -> str:
        return value.replace("-", "").lower() if value else ""

    # Jobs (FK PROTECT) — orphans get reassigned to the sentinel; non-orphans
    # are normalised in place.
    all_jobs = list(Job.objects.values_list("pk", "user_id"))
    orphan_job_pks = [pk for pk, user in all_jobs if _norm(user) not in valid_ids]
    if orphan_job_pks:
        sentinel, _ = User.objects.get_or_create(
            email="seed@local",
            defaults={
                "is_active": False,
                "is_staff": False,
                "is_superuser": False,
                "is_verified": False,
            },
        )
        sentinel_hex = sentinel.id.hex
        for pk, user in all_jobs:
            if pk in orphan_job_pks:
                Job.objects.filter(pk=pk).update(user_id=sentinel_hex)
            else:
                normalised = _norm(user)
                if user != normalised:
                    Job.objects.filter(pk=pk).update(user_id=normalised)
    else:
        for pk, user in all_jobs:
            normalised = _norm(user)
            if user != normalised:
                Job.objects.filter(pk=pk).update(user_id=normalised)

    # AuditEntry (FK SET_NULL) — orphans NULLed in place; non-orphans normalised.
    for record in AuditEntry.objects.all():
        if not record.actor_user_id:
            continue
        normalised = _norm(record.actor_user_id)
        if normalised not in valid_ids:
            record.actor_user_id = None
            record.save(update_fields=["actor_user_id"])
        elif record.actor_user_id != normalised:
            record.actor_user_id = normalised
            record.save(update_fields=["actor_user_id"])

    # IdempotencyKey -> UUIDField. Drop rows with non-UUID values; normalise
    # the rest. Internal cache, safe to lose stale entries.
    for record in IdempotencyKey.objects.all():
        try:
            uuid.UUID(str(record.user_id))
        except (ValueError, TypeError):
            record.delete()
            continue
        normalised = _norm(record.user_id)
        if record.user_id != normalised:
            record.user_id = normalised
            record.save(update_fields=["user_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0002_normalize_actor_user_id"),
        ("accounts", "0001_initial"),
        ("catalog", "0003_owner_fk_promotion"),  # the sentinel User may be created there first
    ]

    operations = [
        migrations.RunPython(_cleanup_orphan_user_refs, reverse_code=migrations.RunPython.noop),
        # --- Job.user_id -> Job.user (FK + PROTECT) ----------------------
        migrations.AlterField(
            model_name="job",
            name="user_id",
            field=models.ForeignKey(
                db_column="user_id",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="job",
            old_name="user_id",
            new_name="user",
        ),
        # The composite ``ix_jobs_user_status`` references "user_id" by
        # field name (not column name). Rename the field reference so the
        # Meta.indexes declaration matches the renamed model field.
        migrations.RemoveIndex(
            model_name="job",
            name="ix_jobs_user_status",
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["user", "status"], name="ix_jobs_user_status"),
        ),
        # --- AuditEntry.actor_user_id -> AuditEntry.actor (FK + SET_NULL) -
        migrations.AlterField(
            model_name="auditentry",
            name="actor_user_id",
            field=models.ForeignKey(
                db_column="actor_user_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="auditentry",
            old_name="actor_user_id",
            new_name="actor",
        ),
        # --- IdempotencyKey.user_id -> UUIDField (no FK) -----------------
        migrations.AlterField(
            model_name="idempotencykey",
            name="user_id",
            field=models.UUIDField(db_column="user_id", db_index=True),
        ),
    ]
