"""Single-use account-activation token (mirror of the magic-link signer).

Self-service registration creates the user *inactive* and emails a link
carrying this token; hitting the activation endpoint with a valid token
flips ``is_active``/``is_verified`` on. A distinct salt isolates it from
the password-reset / magic-link namespaces; single-use is enforced by the
TTL on ``unsign`` (no DB tracking), matching the other account tokens.
"""

from __future__ import annotations

from django.core.signing import TimestampSigner

ACTIVATION_SALT = "accounts.activation"
ACTIVATION_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


def make_activation_token(user_id) -> str:
    return TimestampSigner(salt=ACTIVATION_SALT).sign(str(user_id))


def parse_activation_token(token: str) -> str:
    """Return the embedded user_id (str). Raises SignatureExpired / BadSignature."""
    return TimestampSigner(salt=ACTIVATION_SALT).unsign(token, max_age=ACTIVATION_MAX_AGE_SECONDS)
