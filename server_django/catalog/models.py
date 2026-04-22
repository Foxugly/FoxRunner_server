"""Catalog models — Scenario, Slot, ScenarioShare.

Each field mirrors the SQLAlchemy counterpart in ``api/models.py``. Indexes
declared by past Alembic revisions (``20260421_0007``, ``20260421_0009``,
``20260421_0011``) must be reproduced here so ``makemigrations`` stays
drift-free.
"""

from __future__ import annotations

from django.db import models


class Scenario(models.Model):
    scenario_id = models.CharField(max_length=128, unique=True, db_index=True)
    owner_user_id = models.UUIDField(db_index=True)  # Promoted to FK in phase 5
    description = models.TextField(default="", blank=True)
    definition = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scenarios"
        indexes = [
            models.Index(fields=["owner_user_id", "scenario_id"], name="ix_scenario_owner_id"),
        ]

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
    user_id = models.UUIDField(db_index=True)  # Promoted to FK in phase 5

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
