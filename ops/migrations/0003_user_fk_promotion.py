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


class Migration(migrations.Migration):
    dependencies = [
        ("ops", "0002_normalize_actor_user_id"),
        ("accounts", "0001_initial"),
    ]

    operations = [
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
