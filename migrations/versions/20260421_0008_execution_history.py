"""add execution history table

Revision ID: 20260421_0008
Revises: 20260421_0007
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260421_0008"
down_revision = "20260421_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(length=256), nullable=False),
        sa.Column("slot_id", sa.String(length=128), nullable=False),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("execution_id", sa.String(length=128), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("step", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "slot_id", "scenario_id", name="uq_execution_history_identity"),
    )
    op.create_index("ix_execution_history_executed_at", "execution_history", ["executed_at"])
    op.create_index("ix_execution_history_execution_id", "execution_history", ["execution_id"])
    op.create_index("ix_execution_history_scenario_id", "execution_history", ["scenario_id"])
    op.create_index("ix_execution_history_slot_id", "execution_history", ["slot_id"])
    op.create_index("ix_execution_history_slot_key", "execution_history", ["slot_key"])
    op.create_index("ix_execution_history_status", "execution_history", ["status"])


def downgrade() -> None:
    op.drop_index("ix_execution_history_status", table_name="execution_history")
    op.drop_index("ix_execution_history_slot_key", table_name="execution_history")
    op.drop_index("ix_execution_history_slot_id", table_name="execution_history")
    op.drop_index("ix_execution_history_scenario_id", table_name="execution_history")
    op.drop_index("ix_execution_history_execution_id", table_name="execution_history")
    op.drop_index("ix_execution_history_executed_at", table_name="execution_history")
    op.drop_table("execution_history")
