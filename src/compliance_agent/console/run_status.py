"""Fixed, actionable operator messages for safe run-stopping conditions."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunStatusMessage:
    """Trusted UI copy for one internal run error code."""

    title: str
    detail: str
    action_label: str | None = None
    action_href: str | None = None


_MESSAGES = {
    "planner_unavailable": RunStatusMessage(
        title="Local AI could not create the draft",
        detail=(
            "Your Google account settings are not involved, and nothing was sent to Google. "
            "Use the built-in blocked-sender form, which works without Ollama."
        ),
        action_label="Use built-in form",
        action_href="/runs/new#built-in-form",
    ),
    "ui_contract_pack_required": RunStatusMessage(
        title="Admin Console preview is not installed",
        detail=(
            "The validated plan is available, but this build has no supervised Admin Console "
            "contract pack. No Google Workspace settings were changed."
        ),
        action_label="View readiness",
        action_href="/readiness",
    ),
    "accepted_live_runner_required": RunStatusMessage(
        title="Live execution is not installed",
        detail=(
            "This build does not include an accepted live writer. The plan and preview remain "
            "available, but no Google Workspace settings were changed."
        ),
        action_label="View live gate",
        action_href="/contracts",
    ),
    "approval_expired": RunStatusMessage(
        title="Approval expired",
        detail="Run a new preview to refresh the observed state, hashes, and approval window.",
    ),
    "console_restarted": RunStatusMessage(
        title="Console restarted before this step finished",
        detail=(
            "The unfinished local projection was stopped safely. Review the plan before continuing."
        ),
    ),
    "console_restarted_execution_uncertain": RunStatusMessage(
        title="Console restarted during execution",
        detail=(
            "The Google Admin outcome may be uncertain. Do not retry until audit evidence and "
            "the current Gmail settings have been reconciled."
        ),
        action_label="Review audit evidence",
        action_href="/audits",
    ),
    "run_lock_unavailable": RunStatusMessage(
        title="Another browser-backed run holds the lock",
        detail=(
            "Wait for the other run to finish, then create a fresh preview. No new Google "
            "Workspace changes were started by this run."
        ),
        action_label="View activity",
        action_href="/activity",
    ),
}


def resolve_run_status(error_code: str | None) -> RunStatusMessage | None:
    """Return actionable copy without exposing raw exception details."""

    if error_code is None:
        return None
    known = _MESSAGES.get(error_code)
    if known is not None:
        return known
    readable = error_code.replace("_", " ").strip().capitalize()
    return RunStatusMessage(
        title="Run stopped",
        detail=(
            f"The run stopped at: {readable}. No Google Workspace settings were changed. "
            "Review Readiness for the missing prerequisite before trying again."
        ),
        action_label="View readiness",
        action_href="/readiness",
    )
