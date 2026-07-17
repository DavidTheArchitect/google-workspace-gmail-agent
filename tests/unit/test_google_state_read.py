"""Front-end attended read of the current Google state for both surfaces."""

from uuid import uuid4

import pytest

from compliance_agent.application.attended_policy_service import AttendedPolicyPreview
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.reflex_console.state import ConsoleState
from compliance_agent.schemas.changes import ChangeSet, ComplianceChangeSet
from compliance_agent.schemas.compliance import ContentComplianceState
from compliance_agent.schemas.operations import RunMode
from compliance_agent.schemas.plan import (
    ListBlockedSenderRules,
    ListContentComplianceRules,
    TaskPlan,
)
from compliance_agent.settings import Settings
from tests.conftest import domain, owned_state


def _compliance_preview() -> AttendedPolicyPreview:
    state = ContentComplianceState(unmanaged_rule_names=("Legacy quarantine rule",))
    plan = TaskPlan(status="plan", actions=(ListContentComplianceRules(),))
    change = ComplianceChangeSet(before_state=state, expected_after=state)
    return AttendedPolicyPreview(
        run_id=uuid4().hex,
        mode=RunMode.DRY_RUN,
        surface="content_compliance",
        plan=plan,
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(state),
        change_set_hash=canonical_hash(change),
        compliance_before=state,
        compliance_after=state,
        compliance_change=change,
    )


def _standard_preview() -> AttendedPolicyPreview:
    state = owned_state(entries=(domain("blocked.example"),), unmanaged=("Manual rule",))
    plan = TaskPlan(status="plan", actions=(ListBlockedSenderRules(),))
    change = ChangeSet(before_state=state, expected_after=state)
    return AttendedPolicyPreview(
        run_id=uuid4().hex,
        mode=RunMode.DRY_RUN,
        surface="blocked_senders",
        plan=plan,
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(state),
        change_set_hash=canonical_hash(change),
        standard_before=state,
        standard_after=state,
        standard_change=change,
    )


def _install_read(
    monkeypatch: pytest.MonkeyPatch,
    preview: AttendedPolicyPreview,
    *,
    captured: dict[str, object] | None = None,
) -> None:
    async def _fake_require(*_args: object, **_kwargs: object) -> None:
        return None

    async def _fake_preview(
        settings: Settings,
        plan: TaskPlan,
        review: object = None,
    ) -> AttendedPolicyPreview:
        if captured is not None:
            captured["plan"] = plan
            captured["review"] = review
        return preview

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.require_local_model",
        _fake_require,
    )
    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.ATTENDED_POLICY_SERVICE.preview",
        _fake_preview,
    )


@pytest.mark.asyncio
async def test_plan_only_mode_blocks_the_state_read_with_guidance() -> None:
    state = ConsoleState(_reflex_internal_init=True)
    state.run_mode = "plan_only"

    updates = [update async for update in state.assess_google_state("compliance")]

    assert updates == []
    assert "plan-only" in state.google_state_error.casefold()
    assert not state.google_state_in_progress
    assert state.google_state_read_at == ""


@pytest.mark.asyncio
async def test_compliance_read_projects_managed_and_unmanaged_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    _install_read(monkeypatch, _compliance_preview(), captured=captured)
    state = ConsoleState(_reflex_internal_init=True)
    state.run_mode = "dry_run"

    updates = [update async for update in state.assess_google_state("compliance")]

    assert updates == [None]
    plan = captured["plan"]
    assert isinstance(plan, TaskPlan)
    assert isinstance(plan.actions[0], ListContentComplianceRules)
    assert captured["review"] is None
    assert state.google_state_surface_label == "Content compliance"
    assert state.google_state_read_at != ""
    assert state.observed_google_rules == []
    assert state.observed_unmanaged_rules == ["Legacy quarantine rule"]
    assert not state.google_state_in_progress
    assert not state.browser_in_progress
    assert state.status == "Current Google state read"
    assert state.run_history[0]["status"] == "State read"


@pytest.mark.asyncio
async def test_standard_read_projects_rule_rows_with_crud_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = _standard_preview()
    _install_read(monkeypatch, preview, captured=None)
    state = ConsoleState(_reflex_internal_init=True)
    state.run_mode = "dry_run"

    updates = [update async for update in state.assess_google_state("standard")]

    assert updates == [None]
    assert state.google_state_surface_label == "Blocked senders"
    assert len(state.observed_google_rules) == 1
    row = state.observed_google_rules[0]
    rule = preview.standard_before.rules[0]
    assert row["id"] == str(rule.ownership_id)
    assert row["surface"] == "standard"
    assert row["name"] == rule.display_name
    assert row["enabled"] == "Enabled"
    assert "1 blocked list(s)" in row["detail"]
    assert state.observed_unmanaged_rules == ["Manual rule"]


@pytest.mark.asyncio
async def test_read_failure_reports_a_safe_message_and_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_require(*_args: object, **_kwargs: object) -> None:
        return None

    async def _failing_preview(*_args: object, **_kwargs: object) -> AttendedPolicyPreview:
        message = "identity mismatch: wrong administrator"
        raise ValueError(message)

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.require_local_model",
        _fake_require,
    )
    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.ATTENDED_POLICY_SERVICE.preview",
        _failing_preview,
    )
    state = ConsoleState(_reflex_internal_init=True)
    state.run_mode = "dry_run"

    updates = [update async for update in state.assess_google_state("standard")]

    assert updates == [None]
    assert "identity" in state.google_state_error
    assert state.status == "State read blocked"
    assert not state.google_state_in_progress
    assert not state.browser_in_progress


@pytest.mark.asyncio
async def test_locked_workflow_ignores_a_concurrent_read_request() -> None:
    state = ConsoleState(_reflex_internal_init=True)
    state.run_mode = "dry_run"
    state.review_in_progress = True

    updates = [update async for update in state.assess_google_state("standard")]

    assert updates == []
    assert state.google_state_read_at == ""
