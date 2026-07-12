"""Audit, ownership, failure-mapping, composition, and retention integration tests."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from compliance_agent.application.failure_mapping import (
    FailureMappingPreflight,
    FailureMappingReader,
    FailureMappingWriter,
)
from compliance_agent.application.ownership_service import (
    OwnershipLifecycleService,
    OwnershipUpdate,
)
from compliance_agent.application.planning_service import direct_list_plan
from compliance_agent.application.retention_service import (
    AuditRetentionService,
    RetentionCandidate,
)
from compliance_agent.application.ui_contract_service import contract_pack_digest
from compliance_agent.application.workflow_audit_service import (
    PreparedChangeAudit,
    WorkflowAuditService,
)
from compliance_agent.audit.writer import RunAuditWriter, verify_event_chain
from compliance_agent.composition import AcceptedAdapters, compose_compliance_runtime
from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.domain.reconciliation import ReconciliationContext, reconcile_mutation
from compliance_agent.domain.verification import verify_state
from compliance_agent.exceptions import (
    AuditRetentionFailure,
    AuditWriteFailure,
    OwnershipNotEstablished,
    StateReadFailure,
)
from compliance_agent.schemas.changes import DesiredStateResult
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.operations import UiContractPack
from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult
from compliance_agent.schemas.results import MutationResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.settings import Settings
from tests.conftest import SECOND_ID, domain, owned_state


class FixedClock:
    """Return one controlled UTC timestamp."""

    def __init__(self, value: datetime | None = None) -> None:
        self.value = value or datetime(2026, 7, 11, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class MemoryOwnershipStore:
    """Record atomic registry replacements in memory."""

    def __init__(self, registry: OwnershipRegistry | None = None) -> None:
        self.registry = registry or OwnershipRegistry()
        self.saved: list[OwnershipRegistry] = []

    def load(self) -> OwnershipRegistry:
        return self.registry

    def save(self, registry: OwnershipRegistry) -> None:
        self.registry = registry
        self.saved.append(registry)


class StaticPlanner:
    async def create_plan(self, _request_text: str):
        return direct_list_plan()


class ReadyPreflight:
    async def check(self) -> PreflightResult:
        return PreflightResult(
            status="ready",
            identity=PreflightIdentity(
                administrator_email="admin@example.com",
                workspace_domain="example.com",
            ),
        )


class FailingPreflight:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def check(self) -> PreflightResult:
        raise self._error


class StaticReader:
    def __init__(
        self, state: BlockedSenderState | None = None, error: Exception | None = None
    ) -> None:
        self._state = state or BlockedSenderState()
        self._error = error

    async def read_state(self) -> BlockedSenderState:
        if self._error is not None:
            raise self._error
        return self._state


class StaticWriter:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def apply(self, _change_set):
        if self._error is not None:
            raise self._error
        return MutationResult(status="completed", operation="apply")


class FixedIdentifiers:
    def __init__(self, value: UUID = SECOND_ID) -> None:
        self._value = value

    def new(self) -> UUID:
        return self._value


class ToggleFailWriter:
    """Delegate audit writes until a controlled failure is enabled."""

    def __init__(self, writer: RunAuditWriter) -> None:
        self._writer = writer
        self.fail = False

    @property
    def next_sequence(self) -> int:
        return self._writer.next_sequence

    def write_text(self, relative_path: str, content: str) -> Path:
        if self.fail:
            message = "controlled audit failure"
            raise AuditWriteFailure(message)
        return self._writer.write_text(relative_path, content)

    def append(self, event):
        if self.fail:
            message = "controlled audit failure"
            raise AuditWriteFailure(message)
        return self._writer.append(event)


def _prepared_change() -> PreparedChangeAudit:
    plan = direct_list_plan()
    before = BlockedSenderState()
    after = owned_state(ownership_id=SECOND_ID, entries=(domain("new.example"),))
    desired = DesiredStateResult(desired_state=after)
    change_set = calculate_change_set(before, after)
    return PreparedChangeAudit(
        plan=plan,
        current_state=before,
        desired=desired,
        change_set=change_set,
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(before),
        change_set_hash=canonical_hash(change_set),
    )


def _accepted_contract_pack() -> UiContractPack:
    values = {
        "contract_id": SECOND_ID,
        "created_at": FixedClock().now(),
        "status": "accepted",
        "fixture_hashes": ("1" * 64,),
        "contract_names": ("blocked_senders_root",),
    }
    unsigned = UiContractPack.model_construct(**values, accepted_digest=None)
    return UiContractPack(**values, accepted_digest=contract_pack_digest(unsigned))


def test_workflow_auditor_persists_complete_boundary_artifacts(tmp_path: Path) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    auditor = WorkflowAuditService(writer, FixedClock(), "run-1")
    prepared = _prepared_change()
    approval = ConfirmationResponse(
        approved=True,
        approval_id="approval-1",
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    mutation = MutationResult(status="completed", operation="apply")
    verification = verify_state(
        prepared.desired.desired_state,
        prepared.desired.desired_state,
    )
    reconciliation = reconcile_mutation(
        prepared.current_state,
        prepared.desired.desired_state,
        prepared.desired.desired_state,
        ReconciliationContext(
            operation_is_idempotent=True,
            ownership_confirmed=True,
            root_ou_confirmed=True,
            confirmation_valid=True,
        ),
    )

    auditor.record_request("Block new.example")
    auditor.record_plan(prepared.plan)
    auditor.record_preflight(ReadyPreflightResult)
    auditor.record_state("before", prepared.current_state)
    auditor.record_prepared_change(prepared)
    auditor.record_confirmation(approval)
    auditor.record_state("prewrite", prepared.current_state)
    auditor.record_mutation_started(
        prepared.change_set,
        attempt=1,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    auditor.record_mutation_result(
        mutation,
        attempt=1,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    auditor.record_reconciliation(
        reconciliation,
        attempt=1,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    auditor.record_verification(
        verification,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    auditor.record_ownership_update(OwnershipUpdate(added=(SECOND_ID,)))

    expected_files = {
        "request.txt",
        "plan.json",
        "plan.schema.json",
        "preflight.json",
        "before.json",
        "desired.json",
        "desired-result.json",
        "expected_after.json",
        "change_set.json",
        "confirmation.json",
        "prewrite.json",
        "mutation-command-1.json",
        "mutation-result-1.json",
        "reconciliation-1.json",
        "reconciliation-after-1.json",
        "verification.json",
        "after.json",
        "ownership-update.json",
        "run.jsonl",
    }
    assert expected_files <= {path.name for path in writer.run_directory.iterdir()}
    assert not verify_event_chain(writer.run_directory / "run.jsonl")
    assert len((writer.run_directory / "run.jsonl").read_text().splitlines()) == 12


ReadyPreflightResult = PreflightResult(
    status="ready",
    identity=PreflightIdentity(
        administrator_email="admin@example.com",
        workspace_domain="example.com",
    ),
)


def test_ownership_lifecycle_adds_and_removes_only_verified_pairs() -> None:
    store = MemoryOwnershipStore()
    service = OwnershipLifecycleService(store, FixedClock())
    created = _prepared_change().change_set
    matched = verify_state(created.expected_after, created.expected_after)

    added = service.commit_verified(created, matched)
    removed_change = calculate_change_set(created.expected_after, BlockedSenderState())
    removed = service.commit_verified(
        removed_change,
        verify_state(BlockedSenderState(), BlockedSenderState()),
    )

    assert added.added == (SECOND_ID,)
    assert removed.removed == (SECOND_ID,)
    assert store.registry == OwnershipRegistry()
    assert len(store.saved) == 2


def test_ownership_lifecycle_rejects_mismatched_verification_evidence() -> None:
    service = OwnershipLifecycleService(MemoryOwnershipStore(), FixedClock())
    created = _prepared_change().change_set
    unrelated = BlockedSenderState(unmanaged_rule_names=("unrelated",))

    with pytest.raises(OwnershipNotEstablished, match="does not match"):
        service.commit_verified(created, verify_state(unrelated, unrelated))


def test_audit_failure_aborts_before_mutation_and_degrades_after_boundary(tmp_path: Path) -> None:
    delegate = RunAuditWriter(tmp_path / "run")
    writer = ToggleFailWriter(delegate)
    auditor = WorkflowAuditService(writer, FixedClock(), "run-1")
    prepared = _prepared_change()
    writer.fail = True
    with pytest.raises(AuditWriteFailure):
        auditor.record_request("before mutation")
    writer.fail = False
    auditor.record_mutation_started(
        prepared.change_set,
        attempt=1,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )
    writer.fail = True

    auditor.record_mutation_result(
        MutationResult(status="completed", operation="apply"),
        attempt=1,
        plan_hash=prepared.plan_hash,
        before_state_hash=prepared.before_state_hash,
        change_set_hash=prepared.change_set_hash,
    )

    assert auditor.warnings


@pytest.mark.asyncio
async def test_failure_mapping_closes_expected_adapter_failures() -> None:
    failure = OSError("external failure")
    preflight = await FailureMappingPreflight(FailingPreflight(failure)).check()
    with pytest.raises(StateReadFailure) as read_failure:
        await FailureMappingReader(StaticReader(error=failure)).read_state()
    mutation = await FailureMappingWriter(StaticWriter(error=failure)).apply(
        _prepared_change().change_set
    )

    assert preflight.status == "failed"
    assert read_failure.value.__cause__ is failure
    assert mutation.status == "uncertain"
    assert mutation.error_code == "writer_o_s_error"


def test_retention_plans_then_revalidates_explicit_deletion(tmp_path: Path) -> None:
    runs = tmp_path / "audit" / "runs"
    expired = runs / f"20260101T000000Z-{'0' * 32}"
    current = runs / f"20260710T000000Z-{'1' * 32}"
    unrelated = runs / "notes"
    for path in (expired, current, unrelated):
        path.mkdir(parents=True)
    service = AuditRetentionService(tmp_path / "audit", FixedClock(), retention_days=30)

    candidates = service.find_expired()
    deleted = service.delete_expired(candidates)

    assert tuple(candidate.path for candidate in candidates) == (expired,)
    assert deleted == (expired,)
    assert not expired.exists()
    assert current.exists()
    assert unrelated.exists()
    forged = RetentionCandidate(
        path=unrelated,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(AuditRetentionFailure, match="revalidation"):
        service.delete_expired((forged,))


def test_composition_builds_protected_runtime_without_selector_assumptions(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        dry_run=False,
        plan_only=False,
        expected_admin_email="admin@example.com",
        expected_workspace_domain="example.com",
    )
    runtime = compose_compliance_runtime(
        settings,
        AcceptedAdapters(
            planner=StaticPlanner(),
            preflight=ReadyPreflight(),
            current_reader=StaticReader(),
            verification_reader=StaticReader(),
            writer=StaticWriter(),
            contract_pack=_accepted_contract_pack(),
        ),
        clock=FixedClock(),
        identifiers=FixedIdentifiers(),
        repository=tmp_path,
    )

    assert runtime.run_id == SECOND_ID.hex
    assert runtime.run_directory.exists()
    assert runtime.run_directory.parent == settings.audit_dir / "runs"
    assert runtime.workflow is not None
    runtime.close()


def test_composition_rejects_plan_only_runtime(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        plan_only=True,
    )
    adapters = AcceptedAdapters(
        planner=StaticPlanner(),
        preflight=ReadyPreflight(),
        current_reader=StaticReader(),
        verification_reader=StaticReader(),
        writer=StaticWriter(),
        contract_pack=_accepted_contract_pack(),
    )

    with pytest.raises(ValueError, match="PLAN_ONLY"):
        compose_compliance_runtime(settings, adapters)


def test_composition_rejects_dry_run_mutation_runtime(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        plan_only=False,
        dry_run=True,
    )
    adapters = AcceptedAdapters(
        planner=StaticPlanner(),
        preflight=ReadyPreflight(),
        current_reader=StaticReader(),
        verification_reader=StaticReader(),
        writer=StaticWriter(),
        contract_pack=_accepted_contract_pack(),
    )

    with pytest.raises(ValueError, match="DRY_RUN"):
        compose_compliance_runtime(settings, adapters)


def test_retention_candidate_json_is_stable() -> None:
    candidate = RetentionCandidate(
        path=Path(f"C:/audit/runs/20260101T000000Z-{'0' * 32}"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert json.loads(candidate.model_dump_json())["created_at"].endswith("Z")
