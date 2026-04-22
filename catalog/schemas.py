"""Ninja schemas for the catalog endpoints.

Mirrors the Pydantic payloads in ``api/schemas.py`` (``ScenarioPayload``,
``ScenarioUpdatePayload``, ``ScenarioSummaryPayload``, ``SharePayload``,
``ShareListPayload``, ``ShareResponsePayload``, ``DeletedPayload``,
``SlotPayload``, ``SlotUpdatePayload``, ``SlotSummaryPayload``,
``SlotPagePayload``).

The identifier validator preserves the FastAPI regex
``^[A-Za-z0-9_.:-]{1,128}$`` so the OpenAPI contract stays identical.
"""

from __future__ import annotations

import re
from typing import Any

from ninja import Schema
from pydantic import field_validator, model_validator

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


def _validate_identifier(value: str, field_name: str) -> str:
    if not ID_PATTERN.match(value):
        raise ValueError(f"{field_name} doit contenir 1 a 128 caracteres alphanumeriques ou ._:-")
    return value


def _validate_time(value: str, field_name: str) -> None:
    if not TIME_PATTERN.match(value):
        raise ValueError(f"{field_name} doit etre au format HH:MM.")
    hour, minute = value.split(":")
    if int(hour) > 23 or int(minute) > 59:
        raise ValueError(f"{field_name} doit etre une heure valide.")


def _validate_days(value: list[int]) -> list[int]:
    if not value:
        raise ValueError("days ne peut pas etre vide.")
    if any(day < 0 or day > 6 for day in value):
        raise ValueError("days doit contenir uniquement des valeurs entre 0 et 6.")
    return value


class ScenarioIn(Schema):
    scenario_id: str
    owner_user_id: str
    description: str = ""
    definition: dict[str, Any] | None = None

    @field_validator("scenario_id")
    @classmethod
    def _check_scenario_id(cls, value: str) -> str:
        return _validate_identifier(value, "scenario_id")


class ScenarioPatchIn(Schema):
    scenario_id: str | None = None
    owner_user_id: str | None = None
    description: str | None = None
    definition: dict[str, Any] | None = None

    @field_validator("scenario_id")
    @classmethod
    def _check_optional_scenario_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, "scenario_id")


class ScenarioOut(Schema):
    scenario_id: str
    owner_user_id: str
    description: str
    requires_enterprise_network: bool
    before_steps: int
    steps: int
    on_success: int
    on_failure: int
    finally_steps: int
    role: str | None = None
    writable: bool | None = None


class ShareIn(Schema):
    user_id: str


class ShareList(Schema):
    scenario_id: str
    user_ids: list[str]


class ShareOut(Schema):
    scenario_id: str
    user_id: str


class DeletedOut(Schema):
    deleted: str


class SlotIn(Schema):
    slot_id: str
    scenario_id: str
    days: list[int]
    start: str
    end: str
    enabled: bool = True

    @field_validator("slot_id", "scenario_id")
    @classmethod
    def _check_ids(cls, value: str) -> str:
        return _validate_identifier(value, "id")

    @field_validator("days")
    @classmethod
    def _check_days(cls, value: list[int]) -> list[int]:
        return _validate_days(value)

    @model_validator(mode="after")
    def _check_time_window(self) -> SlotIn:
        _validate_time(self.start, "start")
        _validate_time(self.end, "end")
        if self.start >= self.end:
            raise ValueError("start doit etre strictement inferieur a end.")
        return self


class SlotPatchIn(Schema):
    scenario_id: str | None = None
    days: list[int] | None = None
    start: str | None = None
    end: str | None = None
    enabled: bool | None = None

    @field_validator("scenario_id")
    @classmethod
    def _check_optional_scenario_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, "scenario_id")

    @field_validator("days")
    @classmethod
    def _check_optional_days(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _validate_days(value)

    @model_validator(mode="after")
    def _check_optional_time_window(self) -> SlotPatchIn:
        if self.start is not None:
            _validate_time(self.start, "start")
        if self.end is not None:
            _validate_time(self.end, "end")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start doit etre strictement inferieur a end.")
        return self


class SlotOut(Schema):
    slot_id: str
    days: list[int]
    start: str
    end: str
    scenario_id: str
    enabled: bool


class SlotPage(Schema):
    items: list[SlotOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Step collections (Phase 4.5). Mirrors ``StepPayload`` /
# ``StepMutationPayload`` / ``StepDeletePayload`` from ``api/schemas.py``.
# --------------------------------------------------------------------------


class StepIn(Schema):
    step: dict[str, Any]


class StepMutationOut(Schema):
    index: int
    step: dict[str, Any]


class StepDeleteOut(Schema):
    index: int
    deleted: dict[str, Any]


# --------------------------------------------------------------------------
# User-scoped catalog views (Phase 4.6). Mirrors ``ScenarioPagePayload`` /
# ``ScenarioDetailPayload`` from ``api/schemas.py`` plus the ``role`` /
# ``writable`` fields filled by ``catalog.permissions.scenario_role``.
#
# ``role`` is one of ``"superuser"``, ``"owner"``, ``"reader"`` and
# ``writable`` mirrors ``role != "reader"``. Both are non-optional here
# (we always populate them on the user-scoped endpoints) but stay
# defaulted for back-compat with the bare ``ScenarioOut`` shape used by
# the create/update endpoints.
# --------------------------------------------------------------------------


class ScenarioListItem(ScenarioOut):
    """Page item: ``ScenarioOut`` + role + writable, both required."""

    role: str
    writable: bool


class ScenarioListPage(Schema):
    items: list[ScenarioListItem]
    total: int
    limit: int
    offset: int


class ScenarioDetailOut(ScenarioListItem):
    """Single scenario detail: list-item shape + the full DSL definition JSON."""

    definition: dict[str, Any]


class ScenarioDataOut(Schema):
    """Aggregated pushover/network keys read from ``config/scenarios.json``."""

    default_pushover_key: str | None = None
    default_network_key: str | None = None
    pushovers: list[str]
    networks: list[str]


# --------------------------------------------------------------------------
# Planning + sync run (Phase 4.7). Mirrors
# ``RunScenarioResponsePayload`` from ``api/schemas.py``: ``scenario_id``
# is optional so the same shape covers both ``/scenarios/{sid}/run`` and
# ``/run-next`` (the latter omits it). ``PlanOut`` is a passthrough --
# ``SchedulerService.describe_plan_for_scenarios`` already returns a
# loosely-typed dict, mirroring the FastAPI ``response_model=PlanPayload``
# (also a free-form dict).
# --------------------------------------------------------------------------


class RunOut(Schema):
    scenario_id: str | None = None
    dry_run: bool
    exit_code: int
    success: bool


# --------------------------------------------------------------------------
# Execution history (Phase 4.8). Mirrors ``HistoryEntryPayload`` /
# ``HistoryPagePayload`` from ``api/schemas.py``.
#
# Quirks preserved verbatim:
#   * Default ``limit`` is 20 (NOT 100 like other listings) -- matches
#     FastAPI's ``Query(default=20)`` and avoids overwhelming the UI.
#   * ``executed_at`` is serialised as an ISO 8601 UTC string with a ``Z``
#     suffix (never ``None`` -- the DB column is non-nullable).
# --------------------------------------------------------------------------


class HistoryItem(Schema):
    id: int
    slot_key: str
    slot_id: str
    scenario_id: str
    execution_id: str | None = None
    executed_at: str
    status: str
    step: str
    message: str
    updated_at: str | None = None


class HistoryPage(Schema):
    items: list[HistoryItem]
    total: int
    limit: int
    offset: int
