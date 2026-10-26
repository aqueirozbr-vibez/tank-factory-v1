"""UTC timestamps in one place (code-standards.md · Python · Time)."""

from datetime import UTC, datetime


def utc_now() -> str:
    """ISO 8601 UTC with millisecond precision and an explicit offset."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def parse_ts(value: str) -> datetime:
    """Parse a timestamp written by :func:`utc_now` (or any ISO 8601 with offset)."""
    return datetime.fromisoformat(value)
