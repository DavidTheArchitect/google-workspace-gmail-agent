"""Preview and approval evidence for advanced Gmail blockers."""

from typing import Literal, Self

from pydantic import model_validator

from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.changes import ComplianceChangeSet
from compliance_agent.schemas.compliance import ContentComplianceState
from compliance_agent.schemas.plan import TaskPlan


class ComplianceImpactAssessment(FrozenModel):
    """Operator-visible advanced blocker blast radius."""

    level: Literal["standard", "broad", "destructive"]
    rules_created: int
    rules_updated: int
    rules_removed: int
    target_ous: tuple[str, ...]
    directions: tuple[str, ...]
    expression_count: int


class ComplianceDryRunResult(FrozenModel):
    """Complete read-only evidence required before advanced UI approval."""

    status: Literal["preview_ready", "no_change", "blocked"]
    plan: TaskPlan
    current_state: ContentComplianceState | None = None
    desired_state: ContentComplianceState | None = None
    change_set: ComplianceChangeSet | None = None
    impact: ComplianceImpactAssessment | None = None
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest | None = None
    change_set_hash: Sha256Digest | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def require_status_evidence(self) -> Self:
        if self.status == "blocked":
            if not self.reason_code:
                message = "blocked compliance preview requires a reason code"
                raise ValueError(message)
            return self
        required = (
            self.current_state,
            self.desired_state,
            self.change_set,
            self.impact,
            self.before_state_hash,
            self.change_set_hash,
        )
        if any(value is None for value in required):
            message = f"{self.status} compliance preview requires complete evidence"
            raise ValueError(message)
        return self
