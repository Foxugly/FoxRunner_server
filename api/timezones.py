from __future__ import annotations

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv() -> None:
        return None


load_dotenv()

DEFAULT_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Brussels")

COMMON_TIMEZONES = (
    "Europe/Brussels",
    "Europe/Paris",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Madrid",
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
    "Africa/Casablanca",
    "Asia/Dubai",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
)


def validate_timezone_name(value: str | None, *, default: str = DEFAULT_TIMEZONE) -> str:
    timezone_name = (value or default).strip()
    if not timezone_name:
        timezone_name = default
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"timezone_name invalide: {timezone_name}") from exc
    return timezone_name
