"""Typed mutation, reconciliation, verification, and final-report results."""

from typing import Literal

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.changes import StateDifference
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus


class MutationResult(FrozenModel):
    """Infrastructure observation from one mutation attempt."""

    status: Literal["completed", "unchanged", "uncertain", "partial"]
    operation: str
    resource_ownership_ids: tuple[str, ...] = ()
    error_code: str | None = None


class VerificationResult(FrozenModel):
    """Independent comparison of desired and freshly observed state."""

    status: Literal[
        "matched",
        "mismatched",
        "target_missing",
        "duplicate_target",
        "indeterminate",
    ]
    desired_state: BlockedSenderState
    observed_state: BlockedSenderState | None
    differences: tuple[StateDifference, ...] = ()


class ReconciliationDecision(FrozenModel):
    """Deterministic decision after an uncertain mutation response."""

    outcome: Literal[
        "desired_state_present",
        "mutation_not_applied",
        "partially_applied",
        "indeterminate",
    ]
    retry_is_safe: bool
    observed_state: BlockedSenderState | None
    explanation_code: str


class RunResult(FrozenModel):
    """Authoritative machine-readable report source."""

    status: RunStatus
    requested_changes: tuple[str, ...] = ()
    verified_changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    propagation_pending: bool = False
