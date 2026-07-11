"""Deterministic browser-preflight policy and application adapter."""

from collections.abc import Mapping

import pytest

from compliance_agent.application.preflight_service import PreflightService
from compliance_agent.domain.preflight import AdminConsoleObservation, evaluate_preflight


def _observation(**updates: object) -> AdminConsoleObservation:
    values = {
        "host": "admin.google.com",
        "authentication_state": "authenticated",
        "administrator_email": "admin@example.com",
        "workspace_domain": "example.com",
        "has_gmail_settings_privilege": True,
        "gmail_settings_context_confirmed": True,
        "blocked_senders_context_confirmed": True,
        "target_ou": "/",
    }
    values.update(updates)
    return AdminConsoleObservation.model_validate(values)


def test_complete_exact_preflight_evidence_is_ready() -> None:
    result = evaluate_preflight(_observation(), "ADMIN@example.com", "EXAMPLE.com")

    assert result.status == "ready"
    assert result.identity is not None
    assert result.identity.target_ou == "/"


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"host": "evil.example"}, "unexpected_admin_console_host"),
        ({"administrator_email": "other@example.com"}, "wrong_administrator"),
        ({"workspace_domain": None}, "workspace_identity_absent"),
        ({"workspace_domain": "other.example"}, "workspace_identity_mismatch"),
        ({"has_gmail_settings_privilege": False}, "insufficient_gmail_settings_privilege"),
        ({"has_gmail_settings_privilege": None}, "insufficient_gmail_settings_privilege"),
        ({"gmail_settings_context_confirmed": False}, "gmail_settings_context_not_confirmed"),
        ({"blocked_senders_context_confirmed": False}, "blocked_senders_context_not_confirmed"),
        ({"target_ou": "/Sales"}, "root_ou_not_confirmed"),
        ({"target_ou": None}, "root_ou_not_confirmed"),
    ],
)
def test_any_missing_or_mismatched_mutation_evidence_fails_closed(
    updates: Mapping[str, object],
    reason: str,
) -> None:
    result = evaluate_preflight(
        _observation(**dict(updates)),
        "admin@example.com",
        "example.com",
    )

    assert result.status == "failed"
    assert result.reason_code == reason


@pytest.mark.parametrize(
    "authentication_state",
    ["login_required", "account_chooser", "two_step_verification", "session_expired"],
)
def test_authentication_interruptions_request_manual_login(authentication_state: str) -> None:
    result = evaluate_preflight(
        _observation(authentication_state=authentication_state),
        "admin@example.com",
        "example.com",
    )

    assert result.status == "login_required"
    assert result.login_reason == authentication_state


def test_absent_authenticated_identity_requests_login_instead_of_guessing() -> None:
    result = evaluate_preflight(
        _observation(administrator_email=None),
        "admin@example.com",
        "example.com",
    )

    assert result.status == "login_required"
    assert result.login_reason == "identity_absent"


class FakeObserver:
    """Return one controlled observation."""

    def __init__(self, observation: AdminConsoleObservation) -> None:
        self.observation = observation
        self.calls = 0

    async def observe(self) -> AdminConsoleObservation:
        self.calls += 1
        return self.observation


@pytest.mark.asyncio
async def test_preflight_service_reads_once_and_applies_configured_identity_policy() -> None:
    observer = FakeObserver(_observation())
    service = PreflightService(observer, "admin@example.com", "example.com")

    result = await service.check()

    assert result.status == "ready"
    assert observer.calls == 1
