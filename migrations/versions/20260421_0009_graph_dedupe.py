"""add graph notification dedupe constraint

Revision ID: 20260421_0009
Revises: 20260421_0008
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision = "20260421_0009"
down_revision = "20260421_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("uq_graph_notification_dedupe", "graph_notifications", ["subscription_id", "resource", "change_type", "lifecycle_event"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_graph_notification_dedupe", table_name="graph_notifications")
