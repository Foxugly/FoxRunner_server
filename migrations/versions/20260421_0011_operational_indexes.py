"""add operational query indexes

Revision ID: 20260421_0011
Revises: 20260421_0010
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision = "20260421_0011"
down_revision = "20260421_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_execution_history_scenario_executed_at", "execution_history", ["scenario_id", "executed_at"])
    op.create_index("ix_job_events_job_created_at", "job_events", ["job_id", "created_at"])
    op.create_index("ix_slots_scenario_enabled", "slots", ["scenario_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_slots_scenario_enabled", table_name="slots")
    op.drop_index("ix_job_events_job_created_at", table_name="job_events")
    op.drop_index("ix_execution_history_scenario_executed_at", table_name="execution_history")
