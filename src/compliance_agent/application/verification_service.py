"""Fresh-read verification use case."""

from compliance_agent.application.state_read_service import BlockedSenderReader
from compliance_agent.domain.verification import verify_state
from compliance_agent.schemas.results import VerificationResult
from compliance_agent.schemas.state import BlockedSenderState


class VerificationService:
    """Read through an independent adapter and compare through deterministic code."""

    def __init__(self, reader: BlockedSenderReader) -> None:
        self._reader = reader

    async def verify(self, desired_state: BlockedSenderState) -> VerificationResult:
        """Perform a fresh adapter read and compare the complete state."""

        observed_state = await self._reader.read_state()
        return verify_state(desired_state, observed_state)
