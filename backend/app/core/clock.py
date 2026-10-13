"""All timestamps are stored as naive UTC DATETIME(6) values."""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    """Serialise a naive-UTC datetime so browsers convert it to local time."""
    if value is None:
        return None
    return value.isoformat(timespec="microseconds") + "Z"


def ms_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000.0
