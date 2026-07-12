"""Safe local readiness projections for the attended console."""

from datetime import datetime
from pathlib import Path

from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.exceptions import ComplianceAgentError
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.settings import Settings


class ReadinessItem(FrozenModel):
    name: str
    status: str
    detail: str
    blocking: bool


class SystemHealth(FrozenModel):
    """Truthful sidebar health computed from real readiness checks."""

    blocking_count: int
    checked_at: datetime


class ReadinessCache:
    """Short-TTL readiness summary so every page renders honest chrome cheaply."""

    def __init__(self, settings: Settings, clock: Clock, ttl_seconds: float = 30.0) -> None:
        self._settings = settings
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._cached: SystemHealth | None = None

    def health(self) -> SystemHealth:
        now = self._clock.now()
        cached = self._cached
        if cached is not None and (now - cached.checked_at).total_seconds() < self._ttl_seconds:
            return cached
        items = collect_readiness(self._settings)
        fresh = SystemHealth(
            blocking_count=sum(1 for item in items if item.blocking),
            checked_at=now,
        )
        self._cached = fresh
        return fresh


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


def collect_readiness(settings: Settings) -> tuple[ReadinessItem, ...]:
    """Inspect local configuration without opening a browser or making a write."""

    contract_error: str | None = None
    try:
        contract = UiContractStore(settings.state_dir).load()
    except (ComplianceAgentError, OSError, UnicodeError, ValueError) as error:
        contract = None
        contract_error = type(error).__name__
    return (
        ReadinessItem(
            name="Configuration",
            status="ready",
            detail=f"Run mode is {settings.run_mode.value.replace('_', ' ')}.",
            blocking=False,
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
                else "Set CA_EXPECTED_ADMIN_EMAIL before browser-backed work."
            ),
            blocking=not bool(settings.expected_admin_email),
        ),
        ReadinessItem(
            name="Workspace identity",
            status="ready" if settings.expected_workspace_domain else "needed",
            detail=(
                "Expected Workspace is configured."
                if settings.expected_workspace_domain
                else "Set CA_EXPECTED_WORKSPACE_DOMAIN before browser-backed work."
            ),
            blocking=not bool(settings.expected_workspace_domain),
        ),
        ReadinessItem(
            name="UI contract",
            status=(
                "invalid"
                if contract_error
                else "ready"
                if contract and contract.status == "accepted"
                else "evidence_required"
            ),
            detail=(
                f"Contract evidence is invalid ({contract_error}); live behavior remains disabled."
                if contract_error
                else "Accepted contract pack is available."
                if contract and contract.status == "accepted"
                else "Capture sanitized evidence and complete supervised acceptance."
            ),
            blocking=bool(contract_error) or not bool(contract and contract.status == "accepted"),
        ),
    )


def _directory_item(name: str, path: Path) -> ReadinessItem:
    exists = path.exists()
    return ReadinessItem(
        name=name,
        status="ready" if exists else "will_create",
        detail=str(path),
        blocking=False,
    )


def mask_identity(value: str) -> str:
    if not value:
        return "Not configured"
    if "@" not in value:
        return f"{value[:2]}••••"
    local, domain = value.split("@", 1)
    return f"{local[:2]}•••@{domain}"
