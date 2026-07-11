"""End-to-end fixed graph behavior with controlled public-protocol fakes."""

from collections.abc import Sequence
from uuid import UUID

import pytest

from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.planning_service import direct_add_plan, direct_list_plan
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.results import MutationResult, RunResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.workflow.build import WorkflowDependencies, build_compliance_workflow
from compliance_agent.workflow.messages import (
    ClarificationPauseRequest,
    LoginPauseRequest,
    UserRequestMessage,
    WorkflowConfirmationRequest,
)
from tests.conftest import PREFIX, SECOND_ID

THIRD_ID = UUID("2a3c82a1-2d36-42cd-ae97-85ee319bb21d")
FOURTH_ID = UUID("3a3c82a1-2d36-42cd-ae97-85ee319bb21d")


class QueuePlanner:
    """Return controlled plans and retain clarified request text."""

    def __init__(self, plans: Sequence[TaskPlan]) -> None:
        self.plans = list(plans)
        self.requests: list[str] = []

    async def create_plan(self, request_text: str) -> TaskPlan:
        self.requests.append(request_text)
        return self.plans.pop(0)


class QueuePreflight:
    """Return controlled browser-preflight observations."""

    def __init__(self, results: Sequence[PreflightResult]) -> None:
        self.results = list(results)
        self.calls = 0

    async def check(self) -> PreflightResult:
        self.calls += 1
        return self.results.pop(0)


class QueueReader:
    """Return controlled state snapshots in call order."""

    def __init__(self, states: Sequence[BlockedSenderState]) -> None:
        self.states = list(states)
        self.calls = 0

    async def read_state(self) -> BlockedSenderState:
        self.calls += 1
        if not self.states:
            message = "fake reader has no state"
            raise AssertionError(message)
        return self.states.pop(0)


class QueueWriter:
    """Return controlled write observations and record exact change sets."""

    def __init__(self, results: Sequence[MutationResult]) -> None:
        self.results = list(results)
        self.change_sets = []

    async def apply(self, change_set):
        self.change_sets.append(change_set)
        return self.results.pop(0)


class RecordingFinalizer:
    """Record terminal facts without being able to replace them."""

    def __init__(self) -> None:
        self.results: list[RunResult] = []

    async def finalize(self, result: RunResult) -> None:
        self.results.append(result)


class FixedIdentifiers:
    """Return controlled proposed ownership IDs."""

    def __init__(self, identifiers: Sequence[UUID]) -> None:
        self.identifiers = list(identifiers)

    def new(self) -> UUID:
        return self.identifiers.pop(0)


def _ready() -> PreflightResult:
    return PreflightResult(
        status="ready",
        identity=PreflightIdentity(
            administrator_email="admin@example.com",
            workspace_domain="example.com",
        ),
    )


def _add_plan() -> TaskPlan:
    return direct_add_plan((AddressEntry(kind="domain", value="new.example"),), "Rejected")


def _dependencies(  # noqa: PLR0913 - scenario dependencies remain explicit.
    *,
    planner: QueuePlanner,
    preflight: QueuePreflight,
    current_reader: QueueReader,
    verification_reader: QueueReader | None = None,
    writer: QueueWriter | None = None,
    identifiers: Sequence[UUID] = (SECOND_ID,),
    registry: OwnershipRegistry | None = None,
) -> tuple[WorkflowDependencies, RecordingFinalizer, QueueWriter]:
    finalizer = RecordingFinalizer()
    actual_writer = writer or QueueWriter((MutationResult(status="completed", operation="apply"),))
    dependencies = WorkflowDependencies(
        planner=planner,
        preflight=preflight,
        current_reader=current_reader,
        verification_reader=verification_reader or QueueReader((BlockedSenderState(),)),
        writer=actual_writer,
        audit_finalizer=finalizer,
        change_service=ChangeService(FixedIdentifiers(identifiers), PREFIX),
        ownership_registry=registry or OwnershipRegistry(),
        expected_admin_email="admin@example.com",
        audit_directory="C:/audit/run",
    )
    return dependencies, finalizer, actual_writer


def _approval(
    request: WorkflowConfirmationRequest,
    *,
    approved: bool = True,
) -> ConfirmationResponse:
    presentation = request.presentation
    return ConfirmationResponse(
        approved=approved,
        approval_id="approval-1",
        plan_hash=presentation.plan_hash,
        before_state_hash=presentation.before_state_hash,
        change_set_hash=presentation.change_set_hash,
    )


async def _run_until_request(workflow, request_text: str = "Block new.example"):
    result = await workflow.run(UserRequestMessage(request_text=request_text))
    requests = result.get_request_info_events()
    assert len(requests) == 1
    return requests[0]


def _only_output(result) -> RunResult:
    outputs = result.get_outputs()
    assert len(outputs) == 1
    assert isinstance(outputs[0], RunResult)
    return outputs[0]


@pytest.mark.asyncio
async def test_unsupported_plan_finishes_without_preflight_or_browser_reads() -> None:
    plan = TaskPlan(status="unsupported", unsupported_reason="Child OU is unsupported")
    planner = QueuePlanner((plan,))
    preflight = QueuePreflight(())
    reader = QueueReader(())
    dependencies, finalizer, writer = _dependencies(
        planner=planner,
        preflight=preflight,
        current_reader=reader,
    )

    result = await build_compliance_workflow(dependencies).run(
        UserRequestMessage(request_text="Apply to /Sales")
    )
    output = _only_output(result)

    assert output.status == RunStatus.UNSUPPORTED
    assert preflight.calls == 0
    assert reader.calls == 0
    assert not writer.change_sets
    assert finalizer.results == [output]


@pytest.mark.asyncio
async def test_clarification_round_trip_returns_to_planner_then_completes_read_only() -> None:
    clarification = TaskPlan(
        status="clarification_needed",
        clarification_question="Which exact domain?",
    )
    planner = QueuePlanner((clarification, direct_list_plan()))
    dependencies, _, _ = _dependencies(
        planner=planner,
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(),)),
    )
    workflow = build_compliance_workflow(dependencies)

    pending = await _run_until_request(workflow, "Block Roborock")
    assert isinstance(pending.data, ClarificationPauseRequest)
    result = await workflow.run(responses={pending.request_id: "roborock.com"})
    output = _only_output(result)

    assert output.status == RunStatus.NO_CHANGE_REQUIRED
    assert planner.requests[1].endswith("Clarification: roborock.com")


@pytest.mark.asyncio
async def test_manual_login_pause_retries_preflight_without_receiving_credentials() -> None:
    preflight = QueuePreflight(
        (
            PreflightResult(status="login_required", login_reason="two_step_verification"),
            _ready(),
        )
    )
    dependencies, _, _ = _dependencies(
        planner=QueuePlanner((direct_list_plan(),)),
        preflight=preflight,
        current_reader=QueueReader((BlockedSenderState(),)),
    )
    workflow = build_compliance_workflow(dependencies)

    pending = await _run_until_request(workflow, "List blocked senders")
    assert isinstance(pending.data, LoginPauseRequest)
    assert pending.data.reason == "two_step_verification"
    result = await workflow.run(responses={pending.request_id: True})

    assert _only_output(result).status == RunStatus.NO_CHANGE_REQUIRED
    assert preflight.calls == 2


@pytest.mark.asyncio
async def test_declined_login_and_failed_preflight_stop_unchanged() -> None:
    login_dependencies, _, _ = _dependencies(
        planner=QueuePlanner((direct_list_plan(),)),
        preflight=QueuePreflight(
            (PreflightResult(status="login_required", login_reason="login_required"),)
        ),
        current_reader=QueueReader(()),
    )
    login_workflow = build_compliance_workflow(login_dependencies)
    login_pending = await _run_until_request(login_workflow, "List")
    login_result = await login_workflow.run(responses={login_pending.request_id: False})

    assert _only_output(login_result).error_code == "manual_login_not_completed"

    failed_dependencies, _, _ = _dependencies(
        planner=QueuePlanner((direct_list_plan(),)),
        preflight=QueuePreflight((PreflightResult(status="failed", reason_code="wrong_admin"),)),
        current_reader=QueueReader(()),
    )
    failed_result = await build_compliance_workflow(failed_dependencies).run(
        UserRequestMessage(request_text="List")
    )

    assert _only_output(failed_result).error_code == "wrong_admin"


@pytest.mark.asyncio
async def test_exact_confirmation_then_fresh_read_mutates_and_verifies() -> None:
    current_reader = QueueReader((BlockedSenderState(), BlockedSenderState()))
    verification_reader = QueueReader(())
    dependencies, finalizer, writer = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=current_reader,
        verification_reader=verification_reader,
    )
    workflow = build_compliance_workflow(dependencies)

    pending = await _run_until_request(workflow)
    request = pending.data
    assert isinstance(request, WorkflowConfirmationRequest)
    verification_reader.states.append(request.presentation.change_set.expected_after)
    result = await workflow.run(responses={pending.request_id: _approval(request)})
    output = _only_output(result)

    assert output.status == RunStatus.APPLIED_PENDING_PROPAGATION
    assert output.propagation_pending
    assert len(writer.change_sets) == 1
    assert current_reader.calls == 2
    assert finalizer.results == [output]


@pytest.mark.asyncio
async def test_rejected_or_hash_mismatched_confirmation_never_reaches_writer() -> None:
    async def run_response(response_factory):
        dependencies, _, writer = _dependencies(
            planner=QueuePlanner((_add_plan(),)),
            preflight=QueuePreflight((_ready(),)),
            current_reader=QueueReader((BlockedSenderState(),)),
        )
        workflow = build_compliance_workflow(dependencies)
        pending = await _run_until_request(workflow)
        request = pending.data
        assert isinstance(request, WorkflowConfirmationRequest)
        result = await workflow.run(responses={pending.request_id: response_factory(request)})
        return _only_output(result), writer

    rejected, rejected_writer = await run_response(
        lambda request: _approval(request, approved=False)
    )

    def stale_response(request: WorkflowConfirmationRequest) -> ConfirmationResponse:
        return _approval(request).model_copy(update={"change_set_hash": "0" * 64})

    stale, stale_writer = await run_response(stale_response)

    assert rejected.status == RunStatus.CONFIRMATION_REJECTED
    assert stale.error_code == "stale_confirmation"
    assert not rejected_writer.change_sets
    assert not stale_writer.change_sets


@pytest.mark.asyncio
async def test_change_policy_failure_is_terminal_and_unchanged() -> None:
    missing_target_plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "remove_blocked_entries",
                    "target_rule_id": str(SECOND_ID),
                    "entries": [{"kind": "domain", "value": "missing.example"}],
                }
            ],
        }
    )
    dependencies, _, writer = _dependencies(
        planner=QueuePlanner((missing_target_plan,)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(),)),
    )

    result = await build_compliance_workflow(dependencies).run(
        UserRequestMessage(request_text="Remove missing.example")
    )
    output = _only_output(result)

    assert output.status == RunStatus.FAILED_UNCHANGED
    assert output.error_code == "AmbiguousTarget"
    assert not writer.change_sets


@pytest.mark.asyncio
async def test_state_drift_invalidates_approval_and_requires_a_new_confirmation() -> None:
    before = BlockedSenderState()
    drifted = BlockedSenderState(unmanaged_rule_names=("Manual rule added by another admin",))
    current_reader = QueueReader((before, drifted, drifted))
    verification_reader = QueueReader(())
    dependencies, _, writer = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=current_reader,
        verification_reader=verification_reader,
        identifiers=(SECOND_ID, THIRD_ID),
    )
    workflow = build_compliance_workflow(dependencies)

    first_pending = await _run_until_request(workflow)
    first_request = first_pending.data
    assert isinstance(first_request, WorkflowConfirmationRequest)
    second_pause = await workflow.run(
        responses={first_pending.request_id: _approval(first_request)}
    )
    second_requests = second_pause.get_request_info_events()
    assert len(second_requests) == 1
    second_request = second_requests[0].data
    assert isinstance(second_request, WorkflowConfirmationRequest)
    assert (
        second_request.presentation.before_state_hash
        != first_request.presentation.before_state_hash
    )
    assert second_request.prepared_change.current_state == drifted
    verification_reader.states.append(second_request.presentation.change_set.expected_after)

    result = await workflow.run(
        responses={second_requests[0].request_id: _approval(second_request)}
    )

    assert _only_output(result).status == RunStatus.APPLIED_PENDING_PROPAGATION
    assert len(writer.change_sets) == 1
    assert current_reader.calls == 3


@pytest.mark.asyncio
async def test_uncertain_write_with_desired_state_present_is_verified_without_retry() -> None:
    verification_reader = QueueReader(())
    writer = QueueWriter(
        (MutationResult(status="uncertain", operation="save", error_code="timeout"),)
    )
    dependencies, _, _ = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
        verification_reader=verification_reader,
        writer=writer,
    )
    workflow = build_compliance_workflow(dependencies)
    pending = await _run_until_request(workflow)
    request = pending.data
    assert isinstance(request, WorkflowConfirmationRequest)
    verification_reader.states.append(request.presentation.change_set.expected_after)

    result = await workflow.run(responses={pending.request_id: _approval(request)})

    assert _only_output(result).status == RunStatus.APPLIED_PENDING_PROPAGATION
    assert len(writer.change_sets) == 1


@pytest.mark.asyncio
async def test_uncertain_unchanged_write_gets_one_proven_safe_retry() -> None:
    verification_reader = QueueReader((BlockedSenderState(),))
    writer = QueueWriter(
        (
            MutationResult(status="uncertain", operation="save", error_code="timeout"),
            MutationResult(status="completed", operation="save_retry"),
        )
    )
    dependencies, _, _ = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
        verification_reader=verification_reader,
        writer=writer,
    )
    workflow = build_compliance_workflow(dependencies)
    pending = await _run_until_request(workflow)
    request = pending.data
    assert isinstance(request, WorkflowConfirmationRequest)
    verification_reader.states.append(request.presentation.change_set.expected_after)

    result = await workflow.run(responses={pending.request_id: _approval(request)})

    assert _only_output(result).status == RunStatus.APPLIED_PENDING_PROPAGATION
    assert len(writer.change_sets) == 2
    assert writer.change_sets[0] == writer.change_sets[1]


@pytest.mark.asyncio
async def test_second_uncertain_write_is_never_retried_again() -> None:
    writer = QueueWriter(
        (
            MutationResult(status="uncertain", operation="save", error_code="timeout"),
            MutationResult(
                status="uncertain",
                operation="save_retry",
                error_code="timeout",
            ),
        )
    )
    dependencies, _, _ = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
        verification_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
        writer=writer,
    )
    workflow = build_compliance_workflow(dependencies)
    pending = await _run_until_request(workflow)
    request = pending.data
    assert isinstance(request, WorkflowConfirmationRequest)

    result = await workflow.run(responses={pending.request_id: _approval(request)})
    output = _only_output(result)

    assert output.status == RunStatus.FAILED_UNCHANGED
    assert output.error_code == "before_state_unchanged"
    assert len(writer.change_sets) == 2


@pytest.mark.asyncio
async def test_partial_write_and_partial_reconciliation_are_reported_accurately() -> None:
    async def execute(writer: QueueWriter, verification_reader: QueueReader) -> RunResult:
        dependencies, _, _ = _dependencies(
            planner=QueuePlanner((_add_plan(),)),
            preflight=QueuePreflight((_ready(),)),
            current_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
            verification_reader=verification_reader,
            writer=writer,
        )
        workflow = build_compliance_workflow(dependencies)
        pending = await _run_until_request(workflow)
        request = pending.data
        assert isinstance(request, WorkflowConfirmationRequest)
        if not verification_reader.states:
            expected = request.presentation.change_set.expected_after
            verification_reader.states.append(
                BlockedSenderState(address_lists=expected.address_lists)
            )
        result = await workflow.run(responses={pending.request_id: _approval(request)})
        return _only_output(result)

    direct_partial = await execute(
        QueueWriter((MutationResult(status="partial", operation="create", error_code="orphan"),)),
        QueueReader((BlockedSenderState(),)),
    )
    reconciled_partial = await execute(
        QueueWriter(
            (MutationResult(status="uncertain", operation="create", error_code="timeout"),)
        ),
        QueueReader(()),
    )

    assert direct_partial.status == RunStatus.PARTIALLY_APPLIED
    assert direct_partial.error_code == "orphan"
    assert reconciled_partial.status == RunStatus.PARTIALLY_APPLIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation_status", "expected_status"),
    [
        ("completed", RunStatus.INDETERMINATE),
        ("unchanged", RunStatus.FAILED_UNCHANGED),
    ],
)
async def test_verification_mismatch_never_becomes_success(
    mutation_status: str,
    expected_status: RunStatus,
) -> None:
    dependencies, _, _ = _dependencies(
        planner=QueuePlanner((_add_plan(),)),
        preflight=QueuePreflight((_ready(),)),
        current_reader=QueueReader((BlockedSenderState(), BlockedSenderState())),
        verification_reader=QueueReader((BlockedSenderState(),)),
        writer=QueueWriter((MutationResult(status=mutation_status, operation="save"),)),
    )
    workflow = build_compliance_workflow(dependencies)
    pending = await _run_until_request(workflow)
    request = pending.data
    assert isinstance(request, WorkflowConfirmationRequest)

    result = await workflow.run(responses={pending.request_id: _approval(request)})

    assert _only_output(result).status == expected_status
