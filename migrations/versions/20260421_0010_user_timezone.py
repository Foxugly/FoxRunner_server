"""add user timezone

Revision ID: 20260421_0010
Revises: 20260421_0009
Create Date: 2026-04-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260421_0010"
down_revision = "20260421_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("timezone_name", sa.String(length=64), server_default="Europe/Brussels", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user", "timezone_name")
