"""Pure administrator, Workspace, privilege, page, and root-OU preflight policy."""

from typing import Literal

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult


class AdminConsoleObservation(FrozenModel):
    """Typed facts read by a browser observer without making policy decisions."""

    host: str
    authentication_state: Literal[
        "authenticated",
        "login_required",
        "account_chooser",
        "two_step_verification",
        "session_expired",
    ]
    administrator_email: str | None = None
    workspace_domain: str | None = None
    has_gmail_settings_privilege: bool | None = None
    gmail_settings_context_confirmed: bool = False
    blocked_senders_context_confirmed: bool = False
    target_ou: str | None = None


def evaluate_preflight(  # noqa: C901, PLR0911 - ordered fail-closed checks stay explicit.
    observation: AdminConsoleObservation,
    expected_administrator_email: str,
    expected_workspace_domain: str,
) -> PreflightResult:
    """Return a closed result; no partial identity evidence can authorize mutation."""

    if observation.authentication_state != "authenticated":
        return PreflightResult(
            status="login_required",
            login_reason=observation.authentication_state,
        )
    if observation.host.lower() != "admin.google.com":
        return PreflightResult(status="failed", reason_code="unexpected_admin_console_host")
    if observation.administrator_email is None:
        return PreflightResult(status="login_required", login_reason="identity_absent")
    if observation.administrator_email.casefold() != expected_administrator_email.casefold():
        return PreflightResult(status="failed", reason_code="wrong_administrator")
    if observation.workspace_domain is None:
        return PreflightResult(status="failed", reason_code="workspace_identity_absent")
    if observation.workspace_domain.casefold() != expected_workspace_domain.casefold():
        return PreflightResult(status="failed", reason_code="workspace_identity_mismatch")
    if observation.has_gmail_settings_privilege is not True:
        return PreflightResult(status="failed", reason_code="insufficient_gmail_settings_privilege")
    if not observation.gmail_settings_context_confirmed:
        return PreflightResult(status="failed", reason_code="gmail_settings_context_not_confirmed")
    if not observation.blocked_senders_context_confirmed:
        return PreflightResult(status="failed", reason_code="blocked_senders_context_not_confirmed")
    if observation.target_ou != "/":
        return PreflightResult(status="failed", reason_code="root_ou_not_confirmed")
    return PreflightResult(
        status="ready",
        identity=PreflightIdentity(
            administrator_email=observation.administrator_email,
            workspace_domain=observation.workspace_domain,
        ),
    )
