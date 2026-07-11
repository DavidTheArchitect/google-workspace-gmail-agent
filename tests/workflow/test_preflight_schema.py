"""Preflight outcome invariants."""

import pytest
from pydantic import ValidationError

from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult


def test_ready_preflight_requires_only_identity() -> None:
    identity = PreflightIdentity(
        administrator_email="admin@example.com",
        workspace_domain="example.com",
    )

    assert PreflightResult(status="ready", identity=identity).identity == identity

    with pytest.raises(ValidationError, match="requires only"):
        PreflightResult(status="ready", identity=identity, reason_code="wrong_admin")
    with pytest.raises(ValidationError, match="requires only"):
        PreflightResult(status="ready")


def test_login_and_failed_preflight_require_exclusive_reason_evidence() -> None:
    with pytest.raises(ValidationError, match="login reason"):
        PreflightResult(status="login_required")
    with pytest.raises(ValidationError, match="cannot include"):
        PreflightResult(
            status="login_required",
            login_reason="login_required",
            identity=PreflightIdentity(
                administrator_email="admin@example.com",
                workspace_domain="example.com",
            ),
        )
    with pytest.raises(ValidationError, match="reason code"):
        PreflightResult(status="failed")
    with pytest.raises(ValidationError, match="reason code"):
        PreflightResult(
            status="failed",
            reason_code="wrong_workspace",
            login_reason="identity_absent",
        )
