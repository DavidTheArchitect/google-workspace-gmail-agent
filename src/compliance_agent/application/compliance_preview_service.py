"""Exact advanced-blocker preview and one-time approval services."""

import hmac
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from compliance_agent.application.change_service import ChangeService
from compliance_agent.browser.pages.content_compliance import ComplianceBrowserPermit
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import ContentComplianceState
from compliance_agent.schemas.compliance_operations import (
    ComplianceDryRunResult,
    ComplianceImpactAssessment,
)
from compliance_agent.schemas.plan import (
    CreateContentComplianceRule,
    RemoveContentComplianceRule,
    SetContentComplianceRuleEnabled,
    TaskPlan,
    UpdateContentComplianceRule,
)


class PendingComplianceApproval(FrozenModel):
    """Server-owned approval tied to exact advanced preview evidence."""

    run_id: str
    target_ou: str
    plan_hash: str
    before_state_hash: str
    change_set_hash: str
    target_ownership_id: UUID
    operation: Literal["create", "update", "remove", "set_enabled"]
    phrase: str
    expires_at: datetime


class CompliancePreviewService:
    """Create a complete preview from a trusted fresh browser read."""

    def __init__(self, changes: ChangeService) -> None:
        self._changes = changes

    def preview(
        self,
        plan: TaskPlan,
        current_state: ContentComplianceState,
        ownership_registry: OwnershipRegistry,
    ) -> ComplianceDryRunResult:
        """Calculate desired state, impact, and canonical confirmation hashes."""

        desired, change_set = self._changes.calculate_compliance(
            plan, current_state, ownership_registry
        )
        touched = (
            change_set.rules_to_create + change_set.rules_to_update + change_set.rules_to_remove
        )
        target_ous = tuple(sorted({rule.target_ou.path for rule in touched}))
        directions = tuple(
            sorted({direction.value for rule in touched for direction in rule.directions})
        )
        removals = len(change_set.rules_to_remove)
        impact = ComplianceImpactAssessment(
            level=("destructive" if removals else "broad" if len(target_ous) > 1 else "standard"),
            rules_created=len(change_set.rules_to_create),
            rules_updated=len(change_set.rules_to_update),
            rules_removed=removals,
            target_ous=target_ous,
            directions=directions,
            expression_count=sum(len(rule.expressions) for rule in touched),
        )
        return ComplianceDryRunResult(
            status="preview_ready" if change_set.has_mutations else "no_change",
            plan=plan,
            current_state=current_state,
            desired_state=desired,
            change_set=change_set,
            impact=impact,
            plan_hash=canonical_hash(plan),
            before_state_hash=canonical_hash(current_state),
            change_set_hash=canonical_hash(change_set),
        )


class ComplianceApprovalService:
    """Issue and consume one replaceable advanced-blocker approval."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._pending: dict[str, PendingComplianceApproval] = {}

    def issue(
        self,
        run_id: str,
        preview: ComplianceDryRunResult,
        now: datetime,
    ) -> PendingComplianceApproval:
        """Bind an approval phrase to complete current preview evidence."""

        if (
            preview.status != "preview_ready"
            or preview.impact is None
            or len(preview.impact.target_ous) != 1
            or preview.change_set is None
            or preview.before_state_hash is None
            or preview.change_set_hash is None
        ):
            message = "compliance approval requires one-OU complete mutation preview"
            raise ValueError(message)
        touched = (
            preview.change_set.rules_to_create
            + preview.change_set.rules_to_update
            + preview.change_set.rules_to_remove
        )
        mutation_actions = tuple(
            action
            for action in preview.plan.actions
            if isinstance(
                action,
                (
                    CreateContentComplianceRule,
                    UpdateContentComplianceRule,
                    RemoveContentComplianceRule,
                    SetContentComplianceRuleEnabled,
                ),
            )
        )
        if len(touched) != 1 or len(mutation_actions) != 1:
            message = "one browser approval must target exactly one compliance rule mutation"
            raise ValueError(message)
        operation_by_action = {
            CreateContentComplianceRule: "create",
            UpdateContentComplianceRule: "update",
            RemoveContentComplianceRule: "remove",
            SetContentComplianceRuleEnabled: "set_enabled",
        }
        operation = operation_by_action[type(mutation_actions[0])]
        pending = PendingComplianceApproval(
            run_id=run_id,
            target_ou=preview.impact.target_ous[0],
            plan_hash=preview.plan_hash,
            before_state_hash=preview.before_state_hash,
            change_set_hash=preview.change_set_hash,
            target_ownership_id=touched[0].ownership_id,
            operation=operation,
            phrase=f"APPLY {run_id[:4].upper()}",
            expires_at=now + self._ttl,
        )
        self._pending[run_id] = pending
        return pending

    def approve(
        self,
        run_id: str,
        *,
        phrase: str,
        acknowledged: bool,
        approval_id: str,
        now: datetime,
    ) -> ComplianceBrowserPermit:
        """Consume an exact unexpired approval into a browser-only permit."""

        pending = self._pending.get(run_id)
        if pending is None or now >= pending.expires_at:
            self._pending.pop(run_id, None)
            message = "compliance approval is missing or expired"
            raise ValueError(message)
        if not acknowledged or not hmac.compare_digest(phrase.strip(), pending.phrase):
            message = "compliance approval acknowledgement or phrase is incorrect"
            raise ValueError(message)
        del self._pending[run_id]
        return ComplianceBrowserPermit(
            approval_id=approval_id,
            plan_hash=pending.plan_hash,
            before_state_hash=pending.before_state_hash,
            change_set_hash=pending.change_set_hash,
            target_ou=pending.target_ou,
            target_ownership_id=pending.target_ownership_id,
            operation=pending.operation,
            approved=True,
        )
