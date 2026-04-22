"""Catalog models — Scenario, Slot, ScenarioShare.

Each field mirrors the SQLAlchemy counterpart in ``api/models.py``. The
relevant Alembic revisions for catalog tables are ``20260421_0001``
(initial create + 6 indexes, all auto-derived from ``db_index``/``unique``/FK
declarations) and ``20260421_0011`` (the ``ix_slots_scenario_enabled``
composite, which must be declared explicitly in ``Slot.Meta.indexes``).
"""

from __future__ import annotations

from django.db import models


class Scenario(models.Model):
    scenario_id = models.CharField(max_length=128, unique=True, db_index=True)
    owner_user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5 (CharField -> UUIDField -> ForeignKey)
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
    user_id = models.CharField(max_length=320, db_index=True)  # Promoted to FK(User) in phase 5 (CharField -> UUIDField -> ForeignKey)

    class Meta:
        db_table = "scenario_shares"
        constraints = [
            models.UniqueConstraint(fields=["scenario", "user_id"], name="uq_scenario_share_user"),
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
