"""Typed mutation, reconciliation, verification, and final-report results."""

from typing import Literal, Self

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.changes import StateDifference
from compliance_agent.schemas.compliance import ContentComplianceState
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus


class MutationResult(FrozenModel):
    """Infrastructure observation from one mutation attempt."""

    status: Literal["completed", "unchanged", "uncertain", "partial"]
    operation: str = Field(min_length=1, max_length=200)
    resource_ownership_ids: tuple[str, ...] = ()
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_error_evidence(self) -> Self:
        if self.status in {"uncertain", "partial"} and not self.error_code:
            message = f"{self.status} mutation requires an error code"
            raise ValueError(message)
        if self.status in {"completed", "unchanged"} and self.error_code:
            message = f"{self.status} mutation cannot include an error code"
            raise ValueError(message)
        return self


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

    @model_validator(mode="after")
    def validate_status_evidence(self) -> Self:
        if self.status == "matched":
            if self.observed_state is None or self.differences:
                message = "matched verification requires observed state and no differences"
                raise ValueError(message)
            return self
        if not self.differences:
            message = f"{self.status} verification requires at least one difference"
            raise ValueError(message)
        if self.status == "indeterminate":
            if self.observed_state is not None:
                message = "indeterminate verification cannot include trusted observed state"
                raise ValueError(message)
            return self
        if self.observed_state is None:
            message = f"{self.status} verification requires observed state"
            raise ValueError(message)
        return self


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

    @model_validator(mode="after")
    def validate_retry_evidence(self) -> Self:
        if self.retry_is_safe and self.outcome != "mutation_not_applied":
            message = "only a mutation proven not applied can be retried"
            raise ValueError(message)
        if self.observed_state is None and self.outcome != "indeterminate":
            message = "missing reconciliation state must be indeterminate"
            raise ValueError(message)
        return self


class ComplianceVerificationResult(FrozenModel):
    """Independent verification for the advanced Gmail compliance surface."""

    status: Literal["matched", "mismatched", "indeterminate"]
    desired_state: ContentComplianceState
    observed_state: ContentComplianceState | None
    differences: tuple[StateDifference, ...] = ()

    @model_validator(mode="after")
    def validate_status_evidence(self) -> Self:
        if self.status == "matched":
            if self.observed_state is None or self.differences:
                message = "matched compliance verification requires an observed state"
                raise ValueError(message)
            return self
        if not self.differences:
            message = f"{self.status} compliance verification requires differences"
            raise ValueError(message)
        if self.status == "indeterminate" and self.observed_state is not None:
            message = "indeterminate compliance verification cannot trust observed state"
            raise ValueError(message)
        return self


class RunResult(FrozenModel):
    """Authoritative machine-readable report source."""

    status: RunStatus
    requested_changes: tuple[str, ...] = ()
    verified_changes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    propagation_pending: bool = False

    @model_validator(mode="after")
    def validate_propagation_status(self) -> Self:
        should_be_pending = self.status == RunStatus.APPLIED_PENDING_PROPAGATION
        if self.propagation_pending != should_be_pending:
            message = "propagation_pending must match applied_pending_propagation status"
            raise ValueError(message)
        return self
