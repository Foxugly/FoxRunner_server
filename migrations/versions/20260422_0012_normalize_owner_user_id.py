"""normalize owner_user_id to UUID where user exists

Historically, scenarios were seeded from JSON with owner stored as either the
user's email or an opaque identifier like "default", while scenarios created
through the API stored the user's UUID. The ownership check therefore had to
accept both (see api.permissions._is_scenario_owner). This migration rewrites
rows where owner_user_id matches an existing user's email to the corresponding
UUID so future code can rely on UUID-only semantics.

scenario_shares.user_id and audit_log.actor_user_id receive the same treatment
for consistency. IdempotencyRecord.user_id is left alone because its
(user_id, key) uniqueness semantics are application-internal.

Rows whose owner_user_id does not correspond to any user (e.g. "default",
stale emails) are preserved as-is.

Revision ID: 20260422_0012
Revises: 20260421_0011
Create Date: 2026-04-22
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.sql import text

revision = "20260422_0012"
down_revision = "20260421_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Build a mapping from email to UUID. UUIDs are stored as CHAR(32) /
    # native depending on the backend — cast to text to be portable.
    user_rows = list(
        bind.execute(text("SELECT id, email FROM user")).mappings()
    )

    for row in user_rows:
        user_id_raw = row["id"]
        email_raw = row["email"]
        if not email_raw:
            continue
        user_id = _uuid_as_text(user_id_raw, dialect)
        email = str(email_raw)
        params = {"uuid": user_id, "email": email}
        bind.execute(
            text("UPDATE scenarios SET owner_user_id = :uuid WHERE owner_user_id = :email"),
            params,
        )
        bind.execute(
            text("UPDATE scenario_shares SET user_id = :uuid WHERE user_id = :email"),
            params,
        )
        bind.execute(
            text("UPDATE audit_log SET actor_user_id = :uuid WHERE actor_user_id = :email"),
            params,
        )


def downgrade() -> None:
    # This migration is one-way: reversing would require remembering which
    # rows originally held an email and which held the UUID, which the DB
    # does not encode. Downgrading therefore leaves the normalized values in
    # place — the catch-both permission logic from api.permissions continues
    # to work against UUIDs or emails.
    pass


def _uuid_as_text(value: object, dialect: str) -> str:
    if value is None:
        return ""
    if hasattr(value, "hex") and not isinstance(value, (bytes, bytearray)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        # fastapi_users on SQLite stores UUIDs as CHAR(32) hex; some backends
        # may surface them as bytes. Normalize to the canonical 8-4-4-4-12
        # form so comparisons against str(user.id) line up.
        hex_value = value.hex() if isinstance(value, (bytes, bytearray)) else str(value)
        if len(hex_value) == 32:
            return f"{hex_value[0:8]}-{hex_value[8:12]}-{hex_value[12:16]}-{hex_value[16:20]}-{hex_value[20:32]}"
        return hex_value
    return str(value)
