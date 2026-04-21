from __future__ import annotations

from datetime import datetime, timezone


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def db_utc(value: datetime) -> datetime:
    return ensure_utc(value).replace(tzinfo=None)


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(parsed)


def require_utc(value: datetime, *, field_name: str = "datetime") -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} doit inclure une timezone UTC explicite.")
    normalized = value.astimezone(timezone.utc)
    if normalized.utcoffset() != value.utcoffset():
        raise ValueError(f"{field_name} doit etre en UTC.")
    return normalized
