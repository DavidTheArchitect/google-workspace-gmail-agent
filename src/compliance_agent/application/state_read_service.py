"""Typed browser read boundary."""

from typing import Protocol

from compliance_agent.schemas.state import BlockedSenderState


class BlockedSenderReader(Protocol):
    """Read complete normalized state only after browser identity preflight."""

    async def read_state(self) -> BlockedSenderState:
        """Return normalized root-OU state."""


class StateReadService:
    """Thin use case around an injected browser reader."""

    def __init__(self, reader: BlockedSenderReader) -> None:
        self._reader = reader

    async def read(self) -> BlockedSenderState:
        """Read one complete state snapshot."""

        return await self._reader.read_state()
