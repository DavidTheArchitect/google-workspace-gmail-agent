"""Actionable operator-facing run status messages."""

from compliance_agent.console.run_status import resolve_run_status


def test_run_status_resolves_known_and_empty_codes() -> None:
    assert resolve_run_status(None) is None

    planner = resolve_run_status("planner_unavailable")

    assert planner is not None
    assert planner.title == "Local AI could not create the draft"
    assert planner.action_label == "Use built-in form"
    assert planner.action_href == "/runs/new#built-in-form"


def test_run_status_turns_unknown_codes_into_safe_actionable_copy() -> None:
    status = resolve_run_status("selector_not_found")

    assert status is not None
    assert status.title == "Run stopped"
    assert "Selector not found" in status.detail
    assert "No Google Workspace settings were changed" in status.detail
    assert status.action_href == "/readiness"
