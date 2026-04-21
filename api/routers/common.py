from __future__ import annotations

from api.auth import User
from api.catalog import scenario_summary
from api.models import ScenarioRecord
from api.permissions import require_scenario_owner as require_scenario_owner
from api.permissions import scenario_role
from api.serializers import serialize_graph_notification as serialize_graph_notification
from api.time_utils import db_utc, parse_utc


def parse_graph_datetime(value: str):
    return db_utc(parse_utc(value))


def scenario_summary_for_user(record: ScenarioRecord, user: User) -> dict[str, object]:
    role, writable = scenario_role(record, user)
    return {**scenario_summary(record), "role": role, "writable": writable}
