"""Typed browser mutation boundary with no selector or policy knowledge."""

from typing import Protocol

from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.results import MutationResult


class BlockedSenderWriter(Protocol):
    """Apply one previously approved deterministic change set."""

    async def apply(self, change_set: ChangeSet) -> MutationResult:
        """Attempt the exact approved change and return structured observations."""


class MutationService:
    """Thin use case around an injected writer."""

    def __init__(self, writer: BlockedSenderWriter) -> None:
        self._writer = writer

    async def apply(self, change_set: ChangeSet) -> MutationResult:
        """Apply one change set without adding retry behavior."""

        return await self._writer.apply(change_set)
