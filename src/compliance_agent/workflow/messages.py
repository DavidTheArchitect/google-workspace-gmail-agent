"""Typed graph edge and human-interruption payloads."""

from typing import Literal

from pydantic import Field

from compliance_agent.schemas.base import FrozenModel, RequestText
from compliance_agent.schemas.changes import ChangeSet, DesiredStateResult
from compliance_agent.schemas.hitl import ConfirmationRequest, ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.preflight import PreflightIdentity
from compliance_agent.schemas.results import MutationResult, RunResult, VerificationResult
from compliance_agent.schemas.state import BlockedSenderState


class UserRequestMessage(FrozenModel):
    """Workflow entry or clarified request payload."""

    request_text: RequestText


class PlannedTaskMessage(FrozenModel):
    """Validated planner boundary output."""

    request_text: RequestText
    plan: TaskPlan


class ClarificationPauseRequest(FrozenModel):
    """Human clarification request retaining the original user text."""

    original_request_text: RequestText
    reason_code: str = "planner_clarification_required"
    question: str = Field(min_length=1, max_length=2_000)


class PreflightRequestMessage(FrozenModel):
    """Validated plan ready for browser/session preflight."""

    plan: TaskPlan


class LoginPauseRequest(FrozenModel):
    """Manual authentication request retaining the plan that will be retried."""

    plan: TaskPlan
    reason: Literal[
        "login_required",
        "account_chooser",
        "two_step_verification",
        "session_expired",
        "identity_absent",
    ]
    expected_administrator_email: str


class PreflightReadyMessage(FrozenModel):
    """Plan paired with positively established administrator, Workspace, and root OU."""

    plan: TaskPlan
    identity: PreflightIdentity


class CurrentStateMessage(FrozenModel):
    """Plan and identity paired with one normalized state snapshot."""

    plan: TaskPlan
    identity: PreflightIdentity
    current_state: BlockedSenderState


class PreparedChangeMessage(FrozenModel):
    """Deterministic desired state, diff, and confirmation hashes."""

    plan: TaskPlan
    identity: PreflightIdentity
    current_state: BlockedSenderState
    desired: DesiredStateResult
    change_set: ChangeSet
    plan_hash: str
    before_state_hash: str
    change_set_hash: str
    audit_directory: str


class WorkflowConfirmationRequest(FrozenModel):
    """HITL request containing the deterministic presentation and continuation state."""

    presentation: ConfirmationRequest
    prepared_change: PreparedChangeMessage


class ApprovedChangeMessage(FrozenModel):
    """Exact approved change awaiting mandatory pre-write read-back."""

    prepared_change: PreparedChangeMessage
    approval: ConfirmationResponse


class FreshStateMessage(FrozenModel):
    """Approved change paired with the mandatory fresh pre-write observation."""

    approved_change: ApprovedChangeMessage
    observed_state: BlockedSenderState


class MutationCommandMessage(FrozenModel):
    """Hash-valid mutation command with explicit bounded retry count."""

    approved_change: ApprovedChangeMessage
    retry_count: Literal[0, 1] = 0


class MutationResultMessage(FrozenModel):
    """One mutation observation retaining its exact command context."""

    command: MutationCommandMessage
    mutation_result: MutationResult


class ReconciliationRequestMessage(FrozenModel):
    """Uncertain mutation requiring known-entry-point read-back before any retry."""

    mutation: MutationResultMessage


class VerificationRequestMessage(FrozenModel):
    """Completed mutation requiring a separate fresh verification read."""

    mutation: MutationResultMessage


class VerificationResultMessage(FrozenModel):
    """Fresh-read deterministic verification paired with mutation evidence."""

    prepared_change: PreparedChangeMessage
    mutation_result: MutationResult
    verification_result: VerificationResult


class WorkflowTerminalMessage(FrozenModel):
    """Authoritative terminal result awaiting audit finalization."""

    result: RunResult
