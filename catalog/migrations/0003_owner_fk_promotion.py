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


def _reassign_orphan_owners(apps, schema_editor) -> None:
    """Reassign scenario rows whose owner_user_id doesn't match any User.

    Pre-Django seed JSON could store opaque values (``"default"``) or stale
    emails for accounts that no longer exist. After the data migration in
    ``0002`` those values are still present — they were preserved as-is. The
    FK promotion below would crash on them. Reassign each orphan to a
    sentinel User (``seed@local``, inactive, unusable password) so the FK
    constraint applies cleanly. Operators can re-target afterwards.
    """
    User = apps.get_model("accounts", "User")
    Scenario = apps.get_model("catalog", "Scenario")
    ScenarioShare = apps.get_model("catalog", "ScenarioShare")

    # SQLite stores UUIDField as 32-char hex without dashes; CharField source
    # values may have either form. Normalise both sides to hex-no-dashes.
    valid_ids = {uid.hex for uid in User.objects.values_list("id", flat=True)}

    # The CharField column may hold values with OR without dashes; normalise
    # to hex-no-dashes for the comparison and for the resolved valid set.
    def _norm(value: str) -> str:
        return value.replace("-", "").lower() if value else ""

    all_scenarios = list(Scenario.objects.values_list("pk", "owner_user_id"))
    all_shares = list(ScenarioShare.objects.values_list("pk", "user_id"))

    orphan_scenario_pks = [pk for pk, owner in all_scenarios if _norm(owner) not in valid_ids]
    orphan_share_pks = [pk for pk, user in all_shares if _norm(user) not in valid_ids]
    if not orphan_scenario_pks and not orphan_share_pks:
        # Still normalise existing valid values to hex-no-dashes so the
        # SQLite FK check accepts them after AlterField.
        for pk, owner in all_scenarios:
            normalised = _norm(owner)
            if owner != normalised:
                Scenario.objects.filter(pk=pk).update(owner_user_id=normalised)
        for pk, user in all_shares:
            normalised = _norm(user)
            if user != normalised:
                ScenarioShare.objects.filter(pk=pk).update(user_id=normalised)
        return

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

    # Normalise valid rows AND reassign orphans in a single pass.
    for pk, owner in all_scenarios:
        if pk in orphan_scenario_pks:
            Scenario.objects.filter(pk=pk).update(owner_user_id=sentinel_hex)
        else:
            normalised = _norm(owner)
            if owner != normalised:
                Scenario.objects.filter(pk=pk).update(owner_user_id=normalised)
    for pk, user in all_shares:
        if pk in orphan_share_pks:
            ScenarioShare.objects.filter(pk=pk).update(user_id=sentinel_hex)
        else:
            normalised = _norm(user)
            if user != normalised:
                ScenarioShare.objects.filter(pk=pk).update(user_id=normalised)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_normalize_owner_user_id"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_reassign_orphan_owners, reverse_code=migrations.RunPython.noop),
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
