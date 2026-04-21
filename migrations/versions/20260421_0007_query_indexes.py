"""add query performance indexes

Revision ID: 20260421_0007
Revises: 20260421_0006
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision = "20260421_0007"
down_revision = "20260421_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_jobs_status_updated_at", "jobs", ["status", "updated_at"])
    op.create_index("ix_jobs_user_status", "jobs", ["user_id", "status"])
    op.create_index("ix_graph_subscriptions_expiration", "graph_subscriptions", ["expiration_datetime"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_graph_subscriptions_expiration", table_name="graph_subscriptions")
    op.drop_index("ix_jobs_user_status", table_name="jobs")
    op.drop_index("ix_jobs_status_updated_at", table_name="jobs")
