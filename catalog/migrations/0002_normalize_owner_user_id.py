"""Normalize ``owner_user_id`` / ``user_id`` from email to UUID strings.

Mirror of ``migrations/versions/20260422_0012_normalize_owner_user_id.py``
for the Django ORM. Walks every ``User`` and rewrites:

  * ``Scenario.owner_user_id == user.email`` -> ``str(user.id)``
  * ``ScenarioShare.user_id  == user.email`` -> ``str(user.id)``

Idempotent: re-running on already-normalized data is a no-op (the email
string never matches ``user.email`` after the first pass).

One-way: the downgrade is intentionally a noop. Reversing the rewrite
would require remembering which rows originally held an email and which
held the UUID -- the DB does not encode that.

Audit log normalization lives in ``ops/0002_normalize_actor_user_id.py``
(separate app, separate migration, so each app's migration graph stays
clean).
"""

from __future__ import annotations

from django.db import migrations


def normalize_owner_user_id(apps, schema_editor) -> None:
    User = apps.get_model("accounts", "User")
    Scenario = apps.get_model("catalog", "Scenario")
    ScenarioShare = apps.get_model("catalog", "ScenarioShare")

    for user in User.objects.all():
        if not user.email:
            continue
        uuid_str = str(user.id)
        Scenario.objects.filter(owner_user_id=user.email).update(owner_user_id=uuid_str)
        ScenarioShare.objects.filter(user_id=user.email).update(user_id=uuid_str)


def noop(apps, schema_editor) -> None:
    """One-way migration -- see module docstring."""


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(normalize_owner_user_id, noop),
    ]
