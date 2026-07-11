"""Typed browser-session and administrator preflight observations."""

from typing import Literal, Self

from pydantic import model_validator

from compliance_agent.schemas.base import FrozenModel


class PreflightIdentity(FrozenModel):
    """Positively established mutation identity and root-OU scope."""

    administrator_email: str
    workspace_domain: str
    target_ou: Literal["/"] = "/"


class PreflightResult(FrozenModel):
    """Closed preflight outcome returned by the browser adapter."""

    status: Literal["ready", "login_required", "failed"]
    identity: PreflightIdentity | None = None
    reason_code: str | None = None
    login_reason: (
        Literal[
            "login_required",
            "account_chooser",
            "two_step_verification",
            "session_expired",
            "identity_absent",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_outcome_evidence(self) -> Self:
        if self.status == "ready":
            if self.identity is None or self.reason_code or self.login_reason:
                message = "ready preflight requires only established identity evidence"
                raise ValueError(message)
            return self
        if self.identity is not None:
            message = "non-ready preflight cannot include established identity"
            raise ValueError(message)
        if self.status == "login_required":
            if self.login_reason is None or self.reason_code:
                message = "login_required preflight requires one login reason"
                raise ValueError(message)
            return self
        if not self.reason_code or self.login_reason:
            message = "failed preflight requires one deterministic reason code"
            raise ValueError(message)
        return self
