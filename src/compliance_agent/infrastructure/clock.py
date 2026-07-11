"""Injectable UTC clock."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Time source used by audit and ownership boundaries."""

    def now(self) -> datetime:
        """Return a timezone-aware current time."""


class SystemClock:
    """Production UTC clock."""

    def now(self) -> datetime:
        """Return current UTC time."""

        return datetime.now(UTC)
