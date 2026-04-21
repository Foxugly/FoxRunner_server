from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from api.time_utils import require_utc
from api.timezones import validate_timezone_name

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class PageParams(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PageResponsePayload(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class UserPagePayload(BaseModel):
    items: list[UserPayload]
    total: int
    limit: int
    offset: int


class DeletedPayload(BaseModel):
    deleted: str


class AcceptedPayload(BaseModel):
    accepted: int


class HealthPayload(BaseModel):
    status: str


class ReadyPayload(BaseModel):
    status: str
    checks: dict[str, Any]


class StatusPayload(BaseModel):
    status: str
    api_version: str
    environment: str
    ready: bool
    checks: dict[str, Any]


class VersionPayload(BaseModel):
    name: str
    api_version: str
    environment: str


class ClientConfigPayload(BaseModel):
    api_version: str
    environment: str
    default_timezone: str
    features: dict[str, bool]


class ConfigValidationPayload(BaseModel):
    valid: bool
    exit_code: int


class UserPayload(BaseModel):
    id: str
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    timezone_name: str


class FeatureFlagsPayload(BaseModel):
    features: dict[str, bool]


class ScenarioSummaryPayload(BaseModel):
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


class SlotSummaryPayload(BaseModel):
    slot_id: str
    days: list[int]
    start: str
    end: str
    scenario_id: str
    enabled: bool


class ScenarioPagePayload(BaseModel):
    items: list[ScenarioSummaryPayload]
    total: int
    limit: int
    offset: int


class SlotPagePayload(BaseModel):
    items: list[SlotSummaryPayload]
    total: int
    limit: int
    offset: int


class ScenarioDetailPayload(ScenarioSummaryPayload):
    definition: dict[str, Any]


class ShareListPayload(BaseModel):
    scenario_id: str
    user_ids: list[str]


class ShareResponsePayload(BaseModel):
    scenario_id: str
    user_id: str


class StepMutationPayload(BaseModel):
    index: int
    step: dict[str, Any]


class StepDeletePayload(BaseModel):
    index: int
    deleted: dict[str, Any]


class RunScenarioResponsePayload(BaseModel):
    scenario_id: str | None = None
    dry_run: bool
    exit_code: int
    success: bool


class JobPayload(BaseModel):
    job_id: str
    celery_task_id: str | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    kind: str
    user_id: str
    target_id: str
    dry_run: bool
    exit_code: int | None = None
    error: str | None = None
    payload: dict[str, Any]
    result: dict[str, Any]


class JobEventPayload(BaseModel):
    id: int
    job_id: str
    event_type: str
    level: str
    message: str
    step: str | None = None
    payload: dict[str, Any]
    created_at: datetime | None = None


class JobPagePayload(BaseModel):
    items: list[JobPayload]
    total: int
    limit: int
    offset: int


class GraphSubscriptionResponsePayload(BaseModel):
    subscription_id: str
    resource: str
    change_type: str
    notification_url: str
    lifecycle_notification_url: str | None = None
    expiration_datetime: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GraphNotificationPayload(BaseModel):
    id: int
    subscription_id: str
    change_type: str
    resource: str
    tenant_id: str | None = None
    client_state: str | None = None
    lifecycle_event: str | None = None
    raw_payload: dict[str, Any]
    created_at: datetime | None = None


class GraphSubscriptionPagePayload(BaseModel):
    items: list[GraphSubscriptionResponsePayload]
    total: int
    limit: int
    offset: int


class GraphNotificationPagePayload(BaseModel):
    items: list[GraphNotificationPayload]
    total: int
    limit: int
    offset: int


class AppSettingResponsePayload(BaseModel):
    key: str
    value: dict[str, Any]
    description: str
    updated_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AppSettingPagePayload(BaseModel):
    items: list[AppSettingResponsePayload]
    total: int
    limit: int
    offset: int


class AuditPayload(BaseModel):
    id: int
    actor_user_id: str
    action: str
    target_type: str
    target_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: datetime | None = None


class AuditPagePayload(BaseModel):
    items: list[AuditPayload]
    total: int
    limit: int
    offset: int


class ArtifactPayload(BaseModel):
    kind: str
    name: str
    size: int
    updated_at: float | None = None


class ArtifactPagePayload(BaseModel):
    items: list[ArtifactPayload]
    total: int
    limit: int
    offset: int


class HistoryPayload(BaseModel):
    id: int | None = None
    slot_key: str
    slot_id: str
    scenario_id: str
    execution_id: str | None = None
    executed_at: datetime
    status: str
    step: str
    message: str
    updated_at: datetime | None = None


class HistoryPagePayload(BaseModel):
    items: list[HistoryPayload]
    total: int
    limit: int
    offset: int


class AdminConfigChecksPayload(BaseModel):
    status: str
    checks: dict[str, Any]


class AdminDbStatsPayload(BaseModel):
    tables: dict[str, int]
    last_execution_at: datetime | None = None
    failed_jobs: int
    graph_subscriptions_expiring: int


class AdminImportDryRunPayload(BaseModel):
    dry_run: bool
    scenarios: int | None = None
    slots: int | None = None
    imported: bool | None = None


class AdminExportPayload(BaseModel):
    # Scenarios and slots are passed through as DB-sourced documents; both
    # carry backend-defined shapes, so we stop at dict[str, Any] rather than
    # duplicating the scenario/slot DSL schemas here.
    scenarios: dict[str, Any]
    slots: dict[str, Any]


class RetentionPayload(BaseModel):
    removed: dict[str, int]


class MonitoringJobsPayload(BaseModel):
    total: int
    failed: int
    queued: int
    running: int
    stuck: int
    by_status: dict[str, int] = Field(default_factory=dict)
    average_duration_seconds: float | None = None


class MonitoringGraphPayload(BaseModel):
    subscriptions_expiring: int
    expiring_within_hours: int


class MonitoringSummaryPayload(BaseModel):
    jobs: MonitoringJobsPayload
    graph: MonitoringGraphPayload


class PlanPayload(BaseModel):
    generated_at: datetime
    timezone: str
    slot_key: str
    slot_id: str
    scenario_id: str
    scheduled_for: datetime
    requires_enterprise_network: bool
    before_steps: int
    steps: int
    on_success: int
    on_failure: int
    finally_steps: int
    default_pushover_key: str | None = None
    default_network_key: str | None = None
    default_network_available: bool


class TimezoneListPayload(BaseModel):
    default_timezone: str
    timezones: list[str]


class StepPayload(BaseModel):
    step: dict[str, Any] = Field(..., description="Etape DSL brute, par exemple {'type': 'sleep', 'seconds': 1}.")


class GraphSubscriptionPayload(BaseModel):
    resource: str
    change_type: str = "created,updated"
    notification_url: str
    expiration_datetime: datetime
    lifecycle_notification_url: str | None = None

    @field_validator("expiration_datetime")
    @classmethod
    def validate_utc_expiration(cls, value: datetime) -> datetime:
        return require_utc(value, field_name="expiration_datetime")


class ScenarioPayload(BaseModel):
    scenario_id: str
    owner_user_id: str
    description: str = ""
    definition: dict[str, Any] | None = None

    @field_validator("scenario_id")
    @classmethod
    def validate_scenario_id(cls, value: str) -> str:
        return _validate_identifier(value, "scenario_id")


class ScenarioUpdatePayload(BaseModel):
    scenario_id: str | None = None
    owner_user_id: str | None = None
    description: str | None = None
    definition: dict[str, Any] | None = None

    @field_validator("scenario_id")
    @classmethod
    def validate_optional_scenario_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, "scenario_id")


class SharePayload(BaseModel):
    user_id: str


class SlotPayload(BaseModel):
    slot_id: str
    scenario_id: str
    days: list[int]
    start: str
    end: str
    enabled: bool = True

    @field_validator("slot_id", "scenario_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        return _validate_identifier(value, "id")

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("days ne peut pas etre vide.")
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("days doit contenir uniquement des valeurs entre 0 et 6.")
        return value

    @model_validator(mode="after")
    def validate_time_window(self) -> SlotPayload:
        _validate_time(self.start, "start")
        _validate_time(self.end, "end")
        if self.start >= self.end:
            raise ValueError("start doit etre strictement inferieur a end.")
        return self


class SlotUpdatePayload(BaseModel):
    scenario_id: str | None = None
    days: list[int] | None = None
    start: str | None = None
    end: str | None = None
    enabled: bool | None = None

    @field_validator("scenario_id")
    @classmethod
    def validate_optional_scenario_id(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value, "scenario_id")

    @field_validator("days")
    @classmethod
    def validate_optional_days(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        return SlotPayload.validate_days(value)

    @model_validator(mode="after")
    def validate_optional_time_window(self) -> SlotUpdatePayload:
        if self.start is not None:
            _validate_time(self.start, "start")
        if self.end is not None:
            _validate_time(self.end, "end")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start doit etre strictement inferieur a end.")
        return self


class AdminUserUpdatePayload(BaseModel):
    is_active: bool | None = None
    is_superuser: bool | None = None
    is_verified: bool | None = None
    timezone_name: str | None = None

    @field_validator("timezone_name")
    @classmethod
    def validate_optional_timezone(cls, value: str | None) -> str | None:
        return None if value is None else validate_timezone_name(value)


class GraphRenewPayload(BaseModel):
    expiration_datetime: datetime

    @field_validator("expiration_datetime")
    @classmethod
    def validate_utc_expiration(cls, value: datetime) -> datetime:
        return require_utc(value, field_name="expiration_datetime")


class AppSettingPayload(BaseModel):
    value: dict[str, Any]
    description: str = ""


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
