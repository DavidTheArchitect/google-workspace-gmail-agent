"""Safe local readiness projections for the attended console."""

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from compliance_agent.console.capabilities import ConsoleCapabilities

from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.exceptions import ComplianceAgentError
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.operations import RunMode
from compliance_agent.settings import Settings


class ReadinessItem(FrozenModel):
    name: str
    status: str
    detail: str
    blocking: bool
    code_hint: str | None = None
    action_href: str | None = None
    action_label: str | None = None


class SystemHealth(FrozenModel):
    """Truthful sidebar health computed from real readiness checks."""

    blocking_count: int
    checked_at: datetime


class ReadinessCache:
    """Short-TTL readiness summary so every page renders honest chrome cheaply."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        ttl_seconds: float = 30.0,
        capabilities: "ConsoleCapabilities | None" = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._capabilities = capabilities
        self._cached: SystemHealth | None = None

    def health(self) -> SystemHealth:
        now = self._clock.now()
        cached = self._cached
        if cached is not None and (now - cached.checked_at).total_seconds() < self._ttl_seconds:
            return cached
        items = collect_readiness(self._settings, self._capabilities)
        fresh = SystemHealth(
            blocking_count=sum(1 for item in items if item.blocking),
            checked_at=now,
        )
        self._cached = fresh
        return fresh

    def invalidate(self) -> None:
        """Discard cached health after a local configuration update."""

        self._cached = None


_MORNING_START_HOUR = 5
_AFTERNOON_START_HOUR = 12
_EVENING_START_HOUR = 18


def greeting_for_hour(hour: int) -> str:
    """Deterministic operator greeting for a local wall-clock hour."""

    if _MORNING_START_HOUR <= hour < _AFTERNOON_START_HOUR:
        return "Good morning"
    if _AFTERNOON_START_HOUR <= hour < _EVENING_START_HOUR:
        return "Good afternoon"
    return "Good evening"


def collect_readiness(
    settings: Settings,
    capabilities: "ConsoleCapabilities | None" = None,
) -> tuple[ReadinessItem, ...]:
    """Inspect local configuration without opening a browser or making a write."""

    contract_error: str | None = None
    try:
        contract = UiContractStore(settings.state_dir).load()
    except (ComplianceAgentError, OSError, UnicodeError, ValueError) as error:
        contract = None
        contract_error = type(error).__name__
    browser_setup_required = settings.run_mode != RunMode.PLAN_ONLY
    accepted_statuses = (
        {"read_live_validated", "write_live_validated", "accepted"}
        if settings.run_mode == RunMode.DRY_RUN
        else {"accepted"}
    )
    contract_ready = bool(contract and contract.status in accepted_statuses and not contract_error)
    return (
        ReadinessItem(
            name="Launch mode",
            status="ready",
            detail=(
                "Plan-only is ready now; this launch stops after producing a validated plan."
                if settings.run_mode == RunMode.PLAN_ONLY
                else f"This launch is configured for {settings.run_mode.value.replace('_', ' ')}."
            ),
            blocking=False,
            action_href="/setup",
            action_label="Review what this mode can do",
        ),
        _directory_item("Browser profile", settings.profile_dir),
        _directory_item("Audit storage", settings.audit_dir),
        _directory_item("State storage", settings.state_dir),
        ReadinessItem(
            name="Administrator identity",
            status="ready" if settings.expected_admin_email else "needed",
            detail=(
                "Expected administrator is configured."
                if settings.expected_admin_email
                else (
                    "Required for this browser-backed launch. Add it in the setup guide."
                    if browser_setup_required
                    else "Optional for plan-only. Add it before a future Google Admin preview."
                )
            ),
            blocking=browser_setup_required and not bool(settings.expected_admin_email),
            code_hint=None if settings.expected_admin_email else "CA_EXPECTED_ADMIN_EMAIL",
            action_href=None if settings.expected_admin_email else "/setup#google-account",
            action_label=None if settings.expected_admin_email else "Configure Google identity",
        ),
        ReadinessItem(
            name="Workspace identity",
            status="ready" if settings.expected_workspace_domain else "needed",
            detail=(
                "Expected Workspace is configured."
                if settings.expected_workspace_domain
                else (
                    "Required for this browser-backed launch. Add it in the setup guide."
                    if browser_setup_required
                    else "Optional for plan-only. Add it before a future Google Admin preview."
                )
            ),
            blocking=browser_setup_required and not bool(settings.expected_workspace_domain),
            code_hint=(
                None if settings.expected_workspace_domain else "CA_EXPECTED_WORKSPACE_DOMAIN"
            ),
            action_href=None if settings.expected_workspace_domain else "/setup#google-account",
            action_label=None
            if settings.expected_workspace_domain
            else "Configure Google identity",
        ),
        ReadinessItem(
            name="Google Admin interface evidence",
            status=(
                "invalid" if contract_error else "ready" if contract_ready else "evidence_required"
            ),
            detail=(
                f"Contract evidence is invalid ({contract_error}); live behavior remains disabled."
                if contract_error
                else "Required interface evidence is available."
                if contract_ready
                else (
                    "Required for browser-backed work. Review the supervised setup steps."
                    if browser_setup_required
                    else (
                        "Optional for plan-only. Browser preview and apply stay unavailable "
                        "without it."
                    )
                )
            ),
            blocking=browser_setup_required and not contract_ready,
            action_href=(None if contract_ready else "/contracts"),
            action_label=(None if contract_ready else "Review Google Admin setup"),
        ),
        _capability_item(settings, capabilities, browser_setup_required),
    )


def _capability_item(
    settings: Settings,
    capabilities: "ConsoleCapabilities | None",
    browser_setup_required: bool,
) -> ReadinessItem:
    preview_ready = capabilities is not None and capabilities.preview_service is not None
    live_ready = capabilities is not None and capabilities.live_runner is not None
    ready = preview_ready if settings.run_mode == RunMode.DRY_RUN else live_ready
    if settings.run_mode == RunMode.PLAN_ONLY:
        detail = "Browser-backed work is not applicable to this plan-only launch."
    elif ready:
        detail = (
            "A verified read adapter is installed for preview."
            if settings.run_mode == RunMode.DRY_RUN
            else "Verified preview and live execution adapters are installed."
        )
    else:
        reason = capabilities.unavailable_reason if capabilities is not None else None
        detail = (
            "No verified browser-backed adapter is available; the console remains fail-closed."
            + (f" Reason: {reason.replace('_', ' ')}." if reason else "")
        )
    return ReadinessItem(
        name="Browser-backed capability",
        status=(
            "ready"
            if ready
            else "not_applicable"
            if not browser_setup_required
            else "not_installed"
        ),
        detail=detail,
        blocking=browser_setup_required and not ready,
        action_href="/setup",
        action_label="Review available capabilities",
    )


def _directory_item(name: str, path: Path) -> ReadinessItem:
    exists = path.exists()
    return ReadinessItem(
        name=name,
        status="ready" if exists else "automatic",
        detail=(f"Ready at {path}." if exists else f"Will be created automatically at {path}."),
        blocking=False,
    )


def mask_identity(value: str) -> str:
    if not value:
        return "Not configured"
    if "@" not in value:
        return f"{value[:2]}••••"
    local, domain = value.split("@", 1)
    return f"{local[:2]}•••@{domain}"
