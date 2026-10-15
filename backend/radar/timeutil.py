from __future__ import annotations

from datetime import UTC, date, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp (SQLModel stores aware datetimes and returns UTC)."""
    return datetime.now(UTC)


def today() -> date:
    return utcnow().date()
