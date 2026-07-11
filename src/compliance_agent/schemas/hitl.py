"""Human confirmation and interruption payloads."""

from typing import Literal

from pydantic import Field

from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.changes import ChangeSet


class ConfirmationRequest(FrozenModel):
    """Deterministic information an operator must review before any mutation."""

    administrator_email: str
    workspace_domain: str
    target_ou: Literal["/"] = "/"
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest
    change_set: ChangeSet
    notice_affected_entry_count: int = 0
    audit_directory: str


class ConfirmationResponse(FrozenModel):
    """Approval or rejection tied to the exact hashes shown to the operator."""

    approved: bool
    approval_id: str = Field(min_length=1, max_length=200)
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest


class LoginRequest(FrozenModel):
    """Manual browser-authentication interruption payload."""

    reason: Literal[
        "login_required",
        "account_chooser",
        "two_step_verification",
        "session_expired",
        "identity_absent",
    ]
    expected_administrator_email: str


class ClarificationRequest(FrozenModel):
    """Focused deterministic clarification interruption payload."""

    reason_code: str
    question: str
