"""add graph mail webhook tables

Revision ID: 20260421_0004
Revises: 20260421_0003
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260421_0004"
down_revision = "20260421_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=512), nullable=False),
        sa.Column("change_type", sa.String(length=128), nullable=False),
        sa.Column("notification_url", sa.String(length=1024), nullable=False),
        sa.Column("lifecycle_notification_url", sa.String(length=1024), nullable=True),
        sa.Column("client_state", sa.String(length=256), nullable=True),
        sa.Column("expiration_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_subscriptions_resource", "graph_subscriptions", ["resource"])
    op.create_index("ix_graph_subscriptions_subscription_id", "graph_subscriptions", ["subscription_id"], unique=True)
    op.create_table(
        "graph_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.String(length=128), nullable=False),
        sa.Column("change_type", sa.String(length=128), nullable=False),
        sa.Column("resource", sa.String(length=1024), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("client_state", sa.String(length=256), nullable=True),
        sa.Column("lifecycle_event", sa.String(length=128), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_notifications_change_type", "graph_notifications", ["change_type"])
    op.create_index("ix_graph_notifications_subscription_id", "graph_notifications", ["subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_graph_notifications_subscription_id", table_name="graph_notifications")
    op.drop_index("ix_graph_notifications_change_type", table_name="graph_notifications")
    op.drop_table("graph_notifications")
    op.drop_index("ix_graph_subscriptions_subscription_id", table_name="graph_subscriptions")
    op.drop_index("ix_graph_subscriptions_resource", table_name="graph_subscriptions")
    op.drop_table("graph_subscriptions")
