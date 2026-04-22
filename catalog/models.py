"""Catalog models — Scenario, Slot, ScenarioShare.

Each field mirrors the SQLAlchemy counterpart in ``api/models.py``. The
relevant Alembic revisions for catalog tables are ``20260421_0001``
(initial create + 6 indexes, all auto-derived from ``db_index``/``unique``/FK
declarations), ``20260421_0011`` (the ``ix_slots_scenario_enabled``
composite, which must be declared explicitly in ``Slot.Meta.indexes``),
and ``20260422_0012`` (UUID normalization for ``owner_user_id`` /
``user_id`` -- mirrored on the Django side by ``catalog/0002`` +
``catalog/0003`` which promote those columns to ``ForeignKey(User)``).

After phase 5 the ``owner_user_id`` / ``user_id`` columns are FK-backed
UUIDs. The frontend response contract still surfaces them as
``owner_user_id: str``; serializers cast ``str(scenario.owner_id)`` to
preserve the API shape.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Scenario(models.Model):
    scenario_id = models.CharField(max_length=128, unique=True, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        db_column="owner_user_id",
    )
    description = models.TextField(default="", blank=True)
    definition = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scenarios"

    def __str__(self) -> str:
        return self.scenario_id


class ScenarioShare(models.Model):
    scenario = models.ForeignKey(
        Scenario,
        on_delete=models.CASCADE,
        related_name="shares",
        to_field="scenario_id",
        db_column="scenario_id",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        db_column="user_id",
    )

    class Meta:
        db_table = "scenario_shares"
        constraints = [
            models.UniqueConstraint(fields=["scenario", "user"], name="uq_scenario_share_user"),
        ]


class Slot(models.Model):
    slot_id = models.CharField(max_length=128, unique=True, db_index=True)
    scenario = models.ForeignKey(
        Scenario,
        on_delete=models.CASCADE,
        related_name="slots",
        to_field="scenario_id",
        db_column="scenario_id",
    )
    days = models.JSONField(default=list, blank=True)
    start = models.CharField(max_length=5)
    end = models.CharField(max_length=5)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "slots"
        indexes = [
            # From migrations/versions/20260421_0011_operational_indexes.py
            models.Index(fields=["scenario", "enabled"], name="ix_slots_scenario_enabled"),
        ]
