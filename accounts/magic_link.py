"""Single-use magic-link token (mirror of the password-reset TimestampSigner).

A distinct salt isolates it from the password-reset namespace; single-use is
enforced by the 900s TTL on ``unsign`` (no DB tracking), matching the
password-reset contract.
"""

from __future__ import annotations

from django.core.signing import TimestampSigner

MAGIC_LINK_SALT = "accounts.magic_link"
MAGIC_LINK_MAX_AGE_SECONDS = 15 * 60  # 15 minutes


def make_magic_link_token(user_id) -> str:
    return TimestampSigner(salt=MAGIC_LINK_SALT).sign(str(user_id))


def parse_magic_link_token(token: str) -> str:
    """Return the embedded user_id (str). Raises SignatureExpired / BadSignature."""
    return TimestampSigner(salt=MAGIC_LINK_SALT).unsign(
        token, max_age=MAGIC_LINK_MAX_AGE_SECONDS
    )
