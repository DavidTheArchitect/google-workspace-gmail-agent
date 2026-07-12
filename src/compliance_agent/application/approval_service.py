"""Short-lived server-owned approval envelopes for exact preview evidence."""

import hmac
from datetime import datetime, timedelta

from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.operations import DryRunResult


class PendingApproval(FrozenModel):
    """Trusted approval state retained only inside the attended console process."""

    run_id: str
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest
    phrase: str
    expires_at: datetime


class ApprovalService:
    """Issue and consume exact approvals without trusting browser-supplied hashes."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._pending: dict[str, PendingApproval] = {}

    def issue(self, run_id: str, preview: DryRunResult, now: datetime) -> PendingApproval:
        """Create one replaceable approval for a complete mutation preview."""

        if preview.status != "preview_ready" or any(
            value is None
            for value in (preview.before_state_hash, preview.change_set_hash, preview.change_set)
        ):
            message = "approval requires a complete mutation preview"
            raise ValueError(message)
        approval = PendingApproval(
            run_id=run_id,
            plan_hash=preview.plan_hash,
            before_state_hash=preview.before_state_hash,
            change_set_hash=preview.change_set_hash,
            phrase=f"APPLY {run_id[:4].upper()}",
            expires_at=now + self._ttl,
        )
        self._pending[run_id] = approval
        return approval

    def approve(
        self,
        run_id: str,
        *,
        phrase: str,
        acknowledged: bool,
        approval_id: str,
        now: datetime,
    ) -> ConfirmationResponse:
        """Consume an unexpired approval and construct the authoritative response."""

        approval = self._pending.get(run_id)
        if approval is None or now >= approval.expires_at:
            self._pending.pop(run_id, None)
            message = "approval is missing or expired"
            raise ValueError(message)
        if not acknowledged or not hmac.compare_digest(phrase.strip(), approval.phrase):
            message = "approval acknowledgement or phrase is incorrect"
            raise ValueError(message)
        del self._pending[run_id]
        return ConfirmationResponse(
            approved=True,
            approval_id=approval_id,
            plan_hash=approval.plan_hash,
            before_state_hash=approval.before_state_hash,
            change_set_hash=approval.change_set_hash,
        )

    def get(self, run_id: str, now: datetime) -> PendingApproval | None:
        """Return a still-valid envelope without extending or consuming it."""

        approval = self._pending.get(run_id)
        if approval is not None and now >= approval.expires_at:
            self._pending.pop(run_id, None)
            return None
        return approval

    def cancel(self, run_id: str) -> None:
        """Invalidate approval before mutation begins."""

        self._pending.pop(run_id, None)
