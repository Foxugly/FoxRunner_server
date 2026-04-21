"""initial auth and catalog tables

Revision ID: 20260421_0001
Revises:
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260421_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=1024), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=320), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scenarios_owner_user_id", "scenarios", ["owner_user_id"])
    op.create_index("ix_scenarios_scenario_id", "scenarios", ["scenario_id"], unique=True)
    op.create_table(
        "scenario_shares",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=320), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "user_id", name="uq_scenario_share_user"),
    )
    op.create_index("ix_scenario_shares_scenario_id", "scenario_shares", ["scenario_id"])
    op.create_index("ix_scenario_shares_user_id", "scenario_shares", ["user_id"])
    op.create_table(
        "slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.String(length=128), nullable=False),
        sa.Column("scenario_id", sa.String(length=128), nullable=False),
        sa.Column("days", sa.JSON(), nullable=False),
        sa.Column("start", sa.String(length=5), nullable=False),
        sa.Column("end", sa.String(length=5), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.scenario_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_slots_scenario_id", "slots", ["scenario_id"])
    op.create_index("ix_slots_slot_id", "slots", ["slot_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_slots_slot_id", table_name="slots")
    op.drop_index("ix_slots_scenario_id", table_name="slots")
    op.drop_table("slots")
    op.drop_index("ix_scenario_shares_user_id", table_name="scenario_shares")
    op.drop_index("ix_scenario_shares_scenario_id", table_name="scenario_shares")
    op.drop_table("scenario_shares")
    op.drop_index("ix_scenarios_scenario_id", table_name="scenarios")
    op.drop_index("ix_scenarios_owner_user_id", table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
