"""Incremental protected audit recording across trusted workflow boundaries."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from compliance_agent.application.ownership_service import OwnershipUpdate
from compliance_agent.exceptions import AuditWriteFailure
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.changes import ChangeSet, DesiredStateResult
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.preflight import PreflightResult
from compliance_agent.schemas.results import (
    MutationResult,
    ReconciliationDecision,
    VerificationResult,
)
from compliance_agent.schemas.state import BlockedSenderState

_POST_MUTATION_AUDIT_WARNING = (
    "One or more protected audit writes failed after mutation began; verification continued."
)

type AuditStateStage = Literal["before", "prewrite", "after"]


class AuditWriter(Protocol):
    """Minimal protected persistence boundary required by workflow auditing."""

    @property
    def next_sequence(self) -> int: ...

    def write_text(self, relative_path: str, content: str) -> Path: ...

    def append(self, event: AuditEvent) -> AuditEvent: ...


class PreparedChangeAudit(FrozenModel):
    """Trusted prepared-change facts persisted as one audit boundary."""

    plan: TaskPlan
    current_state: BlockedSenderState
    desired: DesiredStateResult
    change_set: ChangeSet
    plan_hash: str
    before_state_hash: str
    change_set_hash: str


class WorkflowAuditService:
    """Persist artifacts and hash-chained events without making workflow decisions."""

    def __init__(self, writer: AuditWriter, clock: Clock, run_id: str) -> None:
        self._writer = writer
        self._clock = clock
        self._run_id = run_id
        self._mutation_started = False
        self._warnings: set[str] = set()

    @property
    def warnings(self) -> tuple[str, ...]:
        """Return deterministic audit-health warnings accumulated after mutation began."""

        return tuple(sorted(self._warnings))

    @property
    def mutation_started(self) -> bool:
        """Return whether protected evidence proves a mutation attempt began."""

        return self._mutation_started

    def record_request(self, request_text: str) -> None:
        self._record(
            event_type="request_received",
            component="planner",
            outcome="recorded",
            artifacts={"request.txt": request_text.rstrip() + "\n"},
        )

    def record_plan(self, plan: TaskPlan) -> None:
        self._record(
            event_type="plan_validated",
            component="planner",
            outcome=plan.status,
            artifacts={
                "plan.json": _model_json(plan),
                "plan.schema.json": _json_text(TaskPlan.model_json_schema()),
            },
        )

    def record_preflight(self, result: PreflightResult) -> None:
        self._record(
            event_type="preflight_completed",
            component="preflight",
            outcome=result.status,
            artifacts={"preflight.json": _model_json(result)},
            error_code=result.reason_code,
            target_ou=result.identity.target_ou if result.identity else None,
        )

    def record_state(self, stage: AuditStateStage, state: BlockedSenderState) -> None:
        filenames = {
            "before": "before.json",
            "prewrite": "prewrite.json",
            "after": "after.json",
        }
        try:
            filename = filenames[stage]
        except KeyError as error:
            message = f"unsupported audit state stage: {stage}"
            raise ValueError(message) from error
        self._record(
            event_type=f"{stage}_state_observed",
            component="state_reader",
            outcome="recorded",
            artifacts={filename: _model_json(state)},
            target_ou=state.target_ou,
        )

    def record_prepared_change(self, prepared: PreparedChangeAudit) -> None:
        self._record(
            event_type="change_set_prepared",
            component="change_service",
            outcome="mutation_required" if prepared.change_set.has_mutations else "no_change",
            artifacts={
                "plan.json": _model_json(prepared.plan),
                "before.json": _model_json(prepared.current_state),
                "desired.json": _model_json(prepared.desired.desired_state),
                "desired-result.json": _model_json(prepared.desired),
                "expected_after.json": _model_json(prepared.change_set.expected_after),
                "change_set.json": _model_json(prepared.change_set),
            },
            plan_hash=prepared.plan_hash,
            before_state_hash=prepared.before_state_hash,
            change_set_hash=prepared.change_set_hash,
            target_ou=prepared.current_state.target_ou,
        )

    def record_confirmation(self, response: ConfirmationResponse) -> None:
        self._record(
            event_type="confirmation_received",
            component="confirmation",
            outcome="approved" if response.approved else "rejected",
            artifacts={"confirmation.json": _model_json(response)},
            plan_hash=response.plan_hash,
            before_state_hash=response.before_state_hash,
            change_set_hash=response.change_set_hash,
            correlation_id=response.approval_id,
        )

    def record_mutation_started(
        self,
        change_set: ChangeSet,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        self._record(
            event_type="mutation_attempted",
            component="mutation_service",
            outcome=f"attempt_{attempt}",
            artifacts={f"mutation-command-{attempt}.json": _model_json(change_set)},
            plan_hash=plan_hash,
            before_state_hash=before_state_hash,
            change_set_hash=change_set_hash,
            target_ou=change_set.before_state.target_ou,
        )
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
        self._record(
            event_type="mutation_observed",
            component="mutation_service",
            outcome=result.status,
            artifacts={f"mutation-result-{attempt}.json": _model_json(result)},
            plan_hash=plan_hash,
            before_state_hash=before_state_hash,
            change_set_hash=change_set_hash,
            error_code=result.error_code,
        )

    def record_reconciliation(
        self,
        decision: ReconciliationDecision,
        *,
        attempt: int,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        artifacts = {f"reconciliation-{attempt}.json": _model_json(decision)}
        if decision.observed_state is not None:
            artifacts[f"reconciliation-after-{attempt}.json"] = _model_json(decision.observed_state)
        self._record(
            event_type="mutation_reconciled",
            component="reconciliation",
            outcome=decision.outcome,
            artifacts=artifacts,
            plan_hash=plan_hash,
            before_state_hash=before_state_hash,
            change_set_hash=change_set_hash,
            error_code=decision.explanation_code,
        )

    def record_verification(
        self,
        result: VerificationResult,
        *,
        plan_hash: str,
        before_state_hash: str,
        change_set_hash: str,
    ) -> None:
        artifacts = {"verification.json": _model_json(result)}
        if result.observed_state is not None:
            artifacts["after.json"] = _model_json(result.observed_state)
        self._record(
            event_type="verification_completed",
            component="verification_service",
            outcome=result.status,
            artifacts=artifacts,
            plan_hash=plan_hash,
            before_state_hash=before_state_hash,
            change_set_hash=change_set_hash,
            target_ou=result.desired_state.target_ou,
        )

    def record_ownership_update(self, update: OwnershipUpdate) -> None:
        self._record(
            event_type="ownership_registry_reconciled",
            component="ownership_lifecycle_service",
            outcome="updated" if update.added or update.removed else "unchanged",
            artifacts={"ownership-update.json": _model_json(update)},
        )

    def _record(  # noqa: PLR0913 - audit fields map directly to the canonical event schema.
        self,
        *,
        event_type: str,
        component: str,
        outcome: str,
        artifacts: Mapping[str, str],
        plan_hash: str | None = None,
        before_state_hash: str | None = None,
        change_set_hash: str | None = None,
        target_ou: str | None = None,
        error_code: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        try:
            for path, content in artifacts.items():
                self._writer.write_text(path, content)
            self._writer.append(
                AuditEvent(
                    run_id=self._run_id,
                    sequence=self._writer.next_sequence,
                    timestamp=self._clock.now(),
                    event_type=event_type,
                    component=component,
                    outcome=outcome,
                    plan_hash=plan_hash,
                    before_state_hash=before_state_hash,
                    change_set_hash=change_set_hash,
                    target_ou=target_ou,
                    error_code=error_code,
                    correlation_id=correlation_id,
                )
            )
        except AuditWriteFailure:
            if not self._mutation_started:
                raise
            self._warnings.add(_POST_MUTATION_AUDIT_WARNING)


def _model_json(model: BaseModel) -> str:
    return _json_text(model.model_dump(mode="json"))


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
