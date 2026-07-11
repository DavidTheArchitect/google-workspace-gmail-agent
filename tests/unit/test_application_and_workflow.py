"""Thin application services and typed Agent Framework planning graph."""

from uuid import UUID

import pytest

from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.mutation_service import MutationService
from compliance_agent.application.planning_service import (
    PlanningService,
    direct_add_plan,
    direct_list_plan,
    direct_remove_entries_plan,
    direct_remove_rule_plan,
    direct_set_notice_plan,
)
from compliance_agent.application.reporting_service import ReportingService
from compliance_agent.application.state_read_service import StateReadService
from compliance_agent.application.verification_service import VerificationService
from compliance_agent.schemas.results import MutationResult, RunResult
from compliance_agent.schemas.status import RunStatus
from compliance_agent.workflow.build import build_planning_workflow
from compliance_agent.workflow.messages import PlannedTaskMessage, UserRequestMessage
from tests.conftest import OWNERSHIP_ID, PREFIX, SECOND_ID, domain, owned_state, registry_for


class FixedIdentifiers:
    """Return a controlled identifier sequence."""

    def __init__(self, *values: UUID) -> None:
        self.values = list(values)

    def new(self) -> UUID:
        return self.values.pop(0)


class FakeReader:
    """Return one state through the public reader protocol."""

    def __init__(self, state):
        self.state = state

    async def read_state(self):
        return self.state


class FakeWriter:
    """Record one applied change set."""

    def __init__(self) -> None:
        self.applied = None

    async def apply(self, change_set):
        self.applied = change_set
        return MutationResult(status="completed", operation="apply")


class FakePlanner:
    """Return a controlled plan through the graph adapter."""

    async def create_plan(self, request_text: str):
        assert request_text == "List blocked senders"
        return direct_list_plan()


class FakeStructuredResult:
    """Expose one validated plan like the structured planner result."""

    def __init__(self, plan):
        self.plan = plan


class FakeNaturalLanguagePlanner:
    """Return structured planner metadata through the application adapter."""

    async def plan(self, request: str):
        assert request == "List"
        return FakeStructuredResult(direct_list_plan())


def test_direct_plan_builders_share_the_task_plan_boundary() -> None:
    entry = domain("example.com")

    assert direct_add_plan((entry,), "Rejected").actions[0].type == "add_blocked_entries"
    assert (
        direct_remove_entries_plan((entry,), OWNERSHIP_ID).actions[0].type
        == "remove_blocked_entries"
    )
    assert direct_list_plan().actions[0].type == "list_blocked_sender_rules"
    assert direct_set_notice_plan(OWNERSHIP_ID, "New").actions[0].type == "set_rejection_notice"
    assert (
        direct_remove_rule_plan(OWNERSHIP_ID, remove_owned_address_list=True).actions[0].type
        == "remove_blocked_sender_rule"
    )


@pytest.mark.asyncio
async def test_planning_service_returns_only_the_validated_structured_plan() -> None:
    plan = await PlanningService(FakeNaturalLanguagePlanner()).create_plan("List")

    assert plan == direct_list_plan()


def test_change_service_injects_ids_and_returns_desired_state_plus_diff() -> None:
    service = ChangeService(FixedIdentifiers(SECOND_ID), PREFIX)
    plan = direct_add_plan((domain("new.example"),), "Separate")

    desired, change_set = service.calculate(plan, owned_state(), registry_for())

    assert SECOND_ID in {rule.ownership_id for rule in desired.desired_state.rules}
    assert change_set.rules_to_create[0].ownership_id == SECOND_ID


@pytest.mark.asyncio
async def test_state_mutation_and_verification_services_use_public_protocols() -> None:
    state = owned_state()
    reader = FakeReader(state)
    writer = FakeWriter()
    change_set = ChangeService(FixedIdentifiers(SECOND_ID), PREFIX).calculate(
        direct_add_plan((domain("new.example"),), "Separate"),
        state,
        registry_for(),
    )[1]

    assert await StateReadService(reader).read() == state
    mutation = await MutationService(writer).apply(change_set)
    verification = await VerificationService(reader).verify(state)

    assert mutation.status == "completed"
    assert writer.applied == change_set
    assert verification.status == "matched"


def test_reporting_service_renders_authoritative_status_without_model_input() -> None:
    result = RunResult(status=RunStatus.NO_CHANGE_REQUIRED)

    report_json, report_markdown = ReportingService().build(result)

    assert '"status": "no_change_required"' in report_json
    assert "`no_change_required`" in report_markdown


@pytest.mark.asyncio
async def test_agent_framework_graph_passes_typed_messages() -> None:
    workflow = build_planning_workflow(FakePlanner())

    result = await workflow.run(UserRequestMessage(request_text="List blocked senders"))
    outputs = result.get_outputs()

    assert len(outputs) == 1
    assert isinstance(outputs[0], PlannedTaskMessage)
    assert outputs[0].plan.actions[0].type == "list_blocked_sender_rules"
