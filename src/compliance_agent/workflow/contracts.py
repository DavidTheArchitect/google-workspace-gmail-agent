"""Typed boundaries used by the fixed compliance workflow."""

from typing import Protocol

from compliance_agent.application.ownership_service import OwnershipUpdate
from compliance_agent.application.workflow_audit_service import AuditStateStage, PreparedChangeAudit
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.preflight import PreflightResult
from compliance_agent.schemas.results import (
    MutationResult,
    ReconciliationDecision,
    RunResult,
    VerificationResult,
)
from compliance_agent.schemas.state import BlockedSenderState


class PlannerAdapter(Protocol):
    """Return a trusted task plan from one user request."""

    async def create_plan(self, request_text: str) -> TaskPlan:
        """Create and validate one plan."""


class PreflightAdapter(Protocol):
    """Check browser session, privilege, identity, and root-OU evidence."""

    async def check(self) -> PreflightResult:
        """Return one closed preflight result."""


class AuditFinalizer(Protocol):
    """Finalize required audit artifacts before a result becomes workflow output."""

    async def finalize(self, result: RunResult) -> None:
        """Persist terminal evidence without changing authoritative facts."""


class OwnershipLifecycle(Protocol):
    """Commit local ownership evidence only after matched verification."""

    def commit_verified(
        self,
        change_set: ChangeSet,
        verification: VerificationResult,
    ) -> OwnershipUpdate: ...


class NullOwnershipLifecycle:
    """No-op ownership lifecycle for read-only and controlled test compositions."""

    def commit_verified(
        self,
        change_set: ChangeSet,
        verification: VerificationResult,
    ) -> OwnershipUpdate:
        del change_set, verification
        return OwnershipUpdate()


class WorkflowAuditor(Protocol):
    """Persist workflow boundary facts and expose post-mutation audit health."""

    @property
    def warnings(self) -> tuple[str, ...]: ...

    @property
    def mutation_started(self) -> bool: ...

    def record_request(self, request_text: str) -> None: ...

    def record_plan(self, plan: TaskPlan) -> None: ...

    def record_preflight(self, result: PreflightResult) -> None: ...

    def record_state(self, stage: AuditStateStage, state: BlockedSenderState) -> None: ...

    def record_prepared_change(self, prepared: PreparedChangeAudit) -> None: ...

    def record_confirmation(self, response: ConfirmationResponse) -> None: ...

    def record_mutation_started(
        self,
        change_set: ChangeSet,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None: ...

    def record_mutation_result(
        self,
        result: MutationResult,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None: ...

    def record_reconciliation(
        self,
        decision: ReconciliationDecision,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None: ...

    def record_verification(
        self,
        result: VerificationResult,
        *,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None: ...

    def record_ownership_update(self, update: OwnershipUpdate) -> None: ...


class NullWorkflowAuditor:
    """No-op auditor used only by the standalone planning slice."""

    def __init__(self) -> None:
        self._mutation_started = False

    @property
    def warnings(self) -> tuple[str, ...]:
        return ()

    @property
    def mutation_started(self) -> bool:
        return self._mutation_started

    def record_request(self, request_text: str) -> None:
        del request_text

    def record_plan(self, plan: TaskPlan) -> None:
        del plan

    def record_preflight(self, result: PreflightResult) -> None:
        del result

    def record_state(self, stage: AuditStateStage, state: BlockedSenderState) -> None:
        del stage, state

    def record_prepared_change(self, prepared: PreparedChangeAudit) -> None:
        del prepared

    def record_confirmation(self, response: ConfirmationResponse) -> None:
        del response

    def record_mutation_started(
        self,
        change_set: ChangeSet,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        del change_set, attempt, plan_hash, before_state_hash, change_set_hash
        self._mutation_started = True

    def record_mutation_result(
        self,
        result: MutationResult,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        del result, attempt, plan_hash, before_state_hash, change_set_hash

    def record_reconciliation(
        self,
        decision: ReconciliationDecision,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        del decision, attempt, plan_hash, before_state_hash, change_set_hash

    def record_verification(
        self,
        result: VerificationResult,
        *,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        del result, plan_hash, before_state_hash, change_set_hash

    def record_ownership_update(self, update: OwnershipUpdate) -> None:
        del update
