"""Read-only browser-backed preview orchestration with no mutation dependency."""

from dataclasses import dataclass

from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.impact_service import assess_impact
from compliance_agent.application.ownership_service import OwnershipRegistryStore
from compliance_agent.application.state_read_service import BlockedSenderReader
from compliance_agent.application.workflow_audit_service import (
    PreparedChangeAudit,
    WorkflowAuditService,
)
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import ComplianceAgentError, StateReadFailure
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.operations import DryRunResult
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.preflight import PreflightResult
from compliance_agent.workflow.contracts import PreflightAdapter


@dataclass(frozen=True, slots=True)
class DryRunDependencies:
    preflight: PreflightAdapter
    reader: BlockedSenderReader
    change_service: ChangeService
    ownership_store: OwnershipRegistryStore
    auditor: WorkflowAuditService
    expected_admin_email: str
    expected_workspace_domain: str


class DryRunService:
    """Produce complete preview evidence and stop before any confirmation or writer exists."""

    def __init__(
        self,
        dependencies: DryRunDependencies,
    ) -> None:
        self._preflight = dependencies.preflight
        self._reader = dependencies.reader
        self._change_service = dependencies.change_service
        self._ownership_store = dependencies.ownership_store
        self._auditor = dependencies.auditor
        self._expected_admin_email = dependencies.expected_admin_email
        self._expected_workspace_domain = dependencies.expected_workspace_domain

    async def preview(self, plan: TaskPlan) -> DryRunResult:
        """Read once, calculate deterministically, persist evidence, and never mutate."""

        plan_hash = canonical_hash(plan)
        self._auditor.record_plan(plan)
        try:
            preflight = await self._preflight.check()
            self._auditor.record_preflight(preflight)
            reason = self._preflight_block_reason(preflight)
            if reason is not None:
                return DryRunResult(
                    status="blocked",
                    plan=plan,
                    plan_hash=plan_hash,
                    reason_code=reason,
                )
            current = await self._reader.read_state()
            self._auditor.record_state("before", current)
            registry = self._ownership_store.load()
            desired, change_set = self._change_service.calculate(plan, current, registry)
        except (ComplianceAgentError, OSError, StateReadFailure) as error:
            return DryRunResult(
                status="blocked",
                plan=plan,
                plan_hash=plan_hash,
                reason_code=type(error).__name__,
            )
        before_hash = canonical_hash(current)
        change_hash = canonical_hash(change_set)
        prepared = PreparedChangeAudit(
            plan=plan,
            current_state=current,
            desired=desired,
            change_set=change_set,
            plan_hash=plan_hash,
            before_state_hash=before_hash,
            change_set_hash=change_hash,
        )
        self._auditor.record_prepared_change(prepared)
        impact = assess_impact(
            change_set,
            desired,
            ownership_verified=_ownership_is_complete(change_set, registry),
        )
        return DryRunResult(
            status="preview_ready" if change_set.has_mutations else "no_change",
            plan=plan,
            current_state=current,
            desired_state=desired.desired_state,
            change_set=change_set,
            impact=impact,
            plan_hash=plan_hash,
            before_state_hash=before_hash,
            change_set_hash=change_hash,
        )

    def _preflight_block_reason(self, result: PreflightResult) -> str | None:
        if result.status != "ready" or result.identity is None:
            return "dry_run_preflight_not_ready"
        if result.identity.administrator_email.casefold() != self._expected_admin_email.casefold():
            return "wrong_administrator"
        if (
            result.identity.workspace_domain.casefold()
            != self._expected_workspace_domain.casefold()
        ):
            return "workspace_identity_mismatch"
        return None


def _ownership_is_complete(change_set: ChangeSet, registry: OwnershipRegistry) -> bool:
    existing_ids = {
        resource.ownership_id
        for resources in (
            change_set.rules_to_update,
            change_set.rules_to_remove,
            change_set.address_lists_to_update,
            change_set.address_lists_to_remove,
        )
        for resource in resources
    }
    return all(registry.find(ownership_id) is not None for ownership_id in existing_ids)
