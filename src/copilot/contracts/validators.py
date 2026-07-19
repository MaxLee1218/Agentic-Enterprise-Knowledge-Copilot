"""Reusable validators for identifiers and UTC timestamps."""

from datetime import UTC, datetime


def validate_identifier(value: str) -> str:
    """Return a normalized stable identifier or reject an empty value."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("identifier must not be empty")
    return normalized


def validate_utc_datetime(value: datetime) -> datetime:
    """Require a timezone-aware datetime whose offset is UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("datetime must use UTC")
    return value


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)
