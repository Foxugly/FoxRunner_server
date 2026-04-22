"""Ninja schemas for the catalog endpoints.

Mirrors the Pydantic payloads in ``api/schemas.py`` (``ScenarioPayload``,
``ScenarioUpdatePayload``, ``ScenarioSummaryPayload``, ``SharePayload``,
``ShareListPayload``, ``ShareResponsePayload``, ``DeletedPayload``).

The identifier validator preserves the FastAPI regex
``^[A-Za-z0-9_.:-]{1,128}$`` so the OpenAPI contract stays identical.
"""

from __future__ import annotations

import re
from typing import Any

from ninja import Schema
from pydantic import field_validator

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _validate_identifier(value: str, field_name: str) -> str:
    if not ID_PATTERN.match(value):
        raise ValueError(f"{field_name} doit contenir 1 a 128 caracteres alphanumeriques ou ._:-")
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
