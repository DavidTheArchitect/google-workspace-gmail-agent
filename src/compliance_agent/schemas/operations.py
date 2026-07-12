"""Operator-console, dry-run, contract, recovery, and propagation models."""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.state import BlockedSenderState


class RunMode(StrEnum):
    """Closed execution modes with one unambiguous safety meaning."""

    PLAN_ONLY = "plan_only"
    DRY_RUN = "dry_run"
    LIVE = "live"


class RunPhase(StrEnum):
    """Operator-facing lifecycle phases for one console run."""

    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    PREFLIGHT = "preflight"
    PREVIEW_READY = "preview_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ImpactAssessment(FrozenModel):
    """Deterministic operator impact facts derived from an exact change set."""

    level: Literal["standard", "broad", "destructive"]
    rules_created: int = Field(ge=0)
    rules_updated: int = Field(ge=0)
    rules_removed: int = Field(ge=0)
    address_lists_created: int = Field(ge=0)
    address_lists_updated: int = Field(ge=0)
    address_lists_removed: int = Field(ge=0)
    affected_entries: int = Field(ge=0)
    root_ou_confirmed: bool
    ownership_verified: bool


class DryRunResult(FrozenModel):
    """Complete read-only preview evidence that can never authorize a write."""

    status: Literal["preview_ready", "no_change", "blocked"]
    plan: TaskPlan
    current_state: BlockedSenderState | None = None
    desired_state: BlockedSenderState | None = None
    change_set: ChangeSet | None = None
    impact: ImpactAssessment | None = None
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest | None = None
    change_set_hash: Sha256Digest | None = None
    reason_code: str | None = None

    @model_validator(mode="after")
    def require_status_evidence(self) -> Self:
        if self.status == "blocked":
            if not self.reason_code:
                message = "blocked dry run requires a reason code"
                raise ValueError(message)
            return self
        if any(
            value is None
            for value in (
                self.current_state,
                self.desired_state,
                self.change_set,
                self.impact,
                self.before_state_hash,
                self.change_set_hash,
            )
        ):
            message = f"{self.status} dry run requires complete preview evidence"
            raise ValueError(message)
        return self


class PhaseTransition(FrozenModel):
    """One recorded operator-visible phase change with its wall-clock time."""

    phase: RunPhase
    at: datetime
    error_code: str | None = None

    @model_validator(mode="after")
    def require_aware_timestamp(self) -> Self:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            message = "phase transition timestamps must be timezone-aware"
            raise ValueError(message)
        return self


class ConsoleRun(FrozenModel):
    """Non-authoritative operator projection over a typed plan or workflow run."""

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    request_text: str = Field(min_length=1, max_length=2_000)
    mode: RunMode
    phase: RunPhase
    created_at: datetime
    updated_at: datetime
    plan: TaskPlan | None = None
    preview: DryRunResult | None = None
    result: RunResult | None = None
    error_code: str | None = None
    source_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    history: tuple[PhaseTransition, ...] = ()

    @model_validator(mode="after")
    def require_aware_timestamps(self) -> Self:
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() is None:
                message = "console run timestamps must be timezone-aware"
                raise ValueError(message)
        if self.updated_at < self.created_at:
            message = "console run update cannot precede creation"
            raise ValueError(message)
        return self


class UiContractPack(FrozenModel):
    """Reviewed fixture and live-acceptance evidence for one Admin UI contract set."""

    schema_version: Literal["1.0"] = "1.0"
    contract_id: UUID
    created_at: datetime
    status: Literal[
        "draft",
        "fixture_validated",
        "read_live_validated",
        "write_live_validated",
        "accepted",
    ]
    fixture_hashes: tuple[Sha256Digest, ...] = ()
    contract_names: tuple[str, ...] = ()
    accepted_digest: Sha256Digest | None = None

    @model_validator(mode="after")
    def require_acceptance_evidence(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            message = "UI contract pack creation time must be timezone-aware"
            raise ValueError(message)
        if self.status == "accepted" and (
            not self.fixture_hashes or not self.contract_names or self.accepted_digest is None
        ):
            message = "accepted UI contract pack requires fixtures, contracts, and digest"
            raise ValueError(message)
        if self.status != "accepted" and self.accepted_digest is not None:
            message = "only an accepted UI contract pack can carry an accepted digest"
            raise ValueError(message)
        return self


class OwnershipHealth(FrozenModel):
    """One read-only ownership reconciliation finding."""

    ownership_id: UUID | None = None
    resource_name: str
    status: Literal[
        "healthy",
        "missing_in_ui",
        "registry_missing",
        "relationship_changed",
        "partial_creation",
        "unmanaged",
    ]
    detail: str
    recoverable_from_audit: bool = False


class PropagationRecord(FrozenModel):
    """Evidence-linked follow-up state after verified UI persistence."""

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal[
        "pending",
        "ui_reconfirmed",
        "mail_flow_verified",
        "expired",
        "failed",
    ] = "pending"
    created_at: datetime
    updated_at: datetime
    due_at: datetime
    ui_recheck_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    mail_flow_audit_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_valid_timeline(self) -> Self:
        values = (self.created_at, self.updated_at, self.due_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in values):
            message = "propagation timestamps must be timezone-aware"
            raise ValueError(message)
        if self.updated_at < self.created_at or self.due_at < self.created_at:
            message = "propagation timeline is inconsistent"
            raise ValueError(message)
        if self.status == "mail_flow_verified" and self.mail_flow_audit_run_id is None:
            message = "mail-flow verification requires a linked audit run"
            raise ValueError(message)
        return self
