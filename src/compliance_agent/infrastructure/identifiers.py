"""Injectable identifier source."""

from typing import Protocol
from uuid import UUID, uuid4


class IdentifierGenerator(Protocol):
    """UUID source injected outside deterministic domain functions."""

    def new(self) -> UUID:
        """Return one new UUID."""


class Uuid4Generator:
    """Production random UUID generator."""

    def new(self) -> UUID:
        """Return a UUID4."""

        return uuid4()
