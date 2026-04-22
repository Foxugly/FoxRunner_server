"""Promote ``Scenario.owner_user_id`` and ``ScenarioShare.user_id`` to FK(User).

After the data normalization in ``0002_normalize_owner_user_id``, every
``owner_user_id`` / ``user_id`` value is either:

  * a UUID string matching ``accounts.User.id``, or
  * an opaque value with no matching User (e.g. ``"default"``, stale
    emails for users that no longer exist).

The schema follow-up flips the columns from ``CharField(max_length=320)``
to ``ForeignKey(User, to_field="id")`` while preserving the original
column names via ``db_column=``. The frontend response contract still
exposes ``owner_user_id: str`` (serialized as ``str(scenario.owner_id)``);
only the storage shape changes.

``Scenario.owner`` uses ``on_delete=PROTECT`` -- deleting a User must
fail loudly if they still own scenarios. ``ScenarioShare.user`` uses
``on_delete=CASCADE`` -- a deleted User loses their share rows
automatically (matches the FastAPI cascade behaviour).

The unique constraint on ScenarioShare stays on ``(scenario, user)`` --
Django auto-renames the underlying field reference.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_normalize_owner_user_id"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="scenarioshare",
            name="uq_scenario_share_user",
        ),
        migrations.AlterField(
            model_name="scenario",
            name="owner_user_id",
            field=models.ForeignKey(
                db_column="owner_user_id",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="scenario",
            old_name="owner_user_id",
            new_name="owner",
        ),
        migrations.AlterField(
            model_name="scenarioshare",
            name="user_id",
            field=models.ForeignKey(
                db_column="user_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="scenarioshare",
            old_name="user_id",
            new_name="user",
        ),
        migrations.AddConstraint(
            model_name="scenarioshare",
            constraint=models.UniqueConstraint(
                fields=("scenario", "user"),
                name="uq_scenario_share_user",
            ),
        ),
    ]
