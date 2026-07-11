"""Application adapter joining browser observations to deterministic preflight policy."""

from typing import Protocol

from compliance_agent.domain.preflight import AdminConsoleObservation, evaluate_preflight
from compliance_agent.schemas.preflight import PreflightResult


class AdminConsoleObserver(Protocol):
    """Read preflight facts without deciding whether they are acceptable."""

    async def observe(self) -> AdminConsoleObservation:
        """Return one complete browser observation."""


class PreflightService:
    """Evaluate observed facts against exact configured identities."""

    def __init__(
        self,
        observer: AdminConsoleObserver,
        expected_administrator_email: str,
        expected_workspace_domain: str,
    ) -> None:
        self._observer = observer
        self._expected_administrator_email = expected_administrator_email
        self._expected_workspace_domain = expected_workspace_domain

    async def check(self) -> PreflightResult:
        """Read once and return deterministic fail-closed policy output."""

        observation = await self._observer.observe()
        return evaluate_preflight(
            observation,
            self._expected_administrator_email,
            self._expected_workspace_domain,
        )
