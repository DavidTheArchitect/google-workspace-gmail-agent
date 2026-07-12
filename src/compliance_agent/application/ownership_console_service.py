"""Console-facing ownership health projections over audited evidence."""

from compliance_agent.application.audit_catalog import AuditCatalog, AuditRunSummary
from compliance_agent.application.ownership_health_service import assess_ownership_health
from compliance_agent.application.ownership_recovery_service import has_verified_creation
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.operations import OwnershipHealth
from compliance_agent.schemas.state import BlockedSenderState


class OwnershipEvidence(FrozenModel):
    """One audited observation the console may honestly reconcile against."""

    run: AuditRunSummary
    state: BlockedSenderState


def latest_observed_state(catalog: AuditCatalog) -> OwnershipEvidence | None:
    """Return the newest integrity-valid run whose after-state parses cleanly."""

    for summary in catalog.list_runs():
        if not summary.integrity_valid:
            continue
        try:
            state = BlockedSenderState.model_validate_json(
                (summary.run_directory / "after.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            continue
        return OwnershipEvidence(run=summary, state=state)
    return None


def health_with_recoverability(
    evidence: OwnershipEvidence,
    registry: OwnershipRegistry,
    managed_prefix: str,
) -> tuple[OwnershipHealth, ...]:
    """Reconcile registry and audited state, marking exactly recoverable findings."""

    findings = assess_ownership_health(evidence.state, registry, managed_prefix)
    return tuple(
        finding.model_copy(update={"recoverable_from_audit": True})
        if (
            finding.status == "registry_missing"
            and finding.ownership_id is not None
            and has_verified_creation(
                evidence.run.run_directory,
                evidence.state,
                finding.ownership_id,
            )
        )
        else finding
        for finding in findings
    )
