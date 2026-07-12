"""Safe local readiness projections for the attended console."""

from pathlib import Path

from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.settings import Settings


class ReadinessItem(FrozenModel):
    name: str
    status: str
    detail: str
    blocking: bool


def collect_readiness(settings: Settings) -> tuple[ReadinessItem, ...]:
    """Inspect local configuration without opening a browser or making a write."""

    contract = UiContractStore(settings.state_dir).load()
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
            status="ready" if contract and contract.status == "accepted" else "evidence_required",
            detail=(
                "Accepted contract pack is available."
                if contract and contract.status == "accepted"
                else "Capture sanitized evidence and complete supervised acceptance."
            ),
            blocking=not bool(contract and contract.status == "accepted"),
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
