import time
from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


def from_struct_time(value: time.struct_time | None) -> datetime | None:
    """Convert a feedparser struct_time (already UTC) to an aware datetime."""
    if value is None:
        return None
    return datetime.fromtimestamp(time.mktime(value) - time.timezone, tz=UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Coerce a naive datetime to UTC (SQLite roundtrips drop tzinfo)."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def from_iso_date(value: str | None) -> datetime | None:
    """Parse an ISO date/datetime string (e.g. trafilatura's '2026-08-11')."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
