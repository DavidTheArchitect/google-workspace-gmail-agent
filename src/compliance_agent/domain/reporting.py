"""Deterministic terminal status selection."""

from compliance_agent.schemas.results import MutationResult, RunResult, VerificationResult
from compliance_agent.schemas.status import RunStatus

_PROPAGATION_WARNING = (
    "The configuration was saved and confirmed through a fresh Admin console read-back. "
    "Gmail enforcement may still be propagating."
)


def determine_run_result(  # noqa: PLR0911 - terminal-state precedence is intentionally explicit.
    mutation: MutationResult | None,
    verification: VerificationResult | None,
    *,
    confirmation_rejected: bool = False,
    unsupported: bool = False,
    no_change_required: bool = False,
) -> RunResult:
    """Return authoritative status without consulting model-authored prose."""

    if unsupported:
        return RunResult(status=RunStatus.UNSUPPORTED)
    if confirmation_rejected:
        return RunResult(status=RunStatus.CONFIRMATION_REJECTED)
    if no_change_required:
        return RunResult(status=RunStatus.NO_CHANGE_REQUIRED)
    if mutation is None:
        return RunResult(status=RunStatus.FAILED_UNCHANGED, error_code="mutation_not_attempted")
    if mutation.status == "partial":
        return RunResult(status=RunStatus.PARTIALLY_APPLIED, error_code=mutation.error_code)
    if mutation.status == "uncertain" and verification is None:
        return RunResult(status=RunStatus.INDETERMINATE, error_code=mutation.error_code)
    if verification is None:
        return RunResult(status=RunStatus.INDETERMINATE, error_code="verification_missing")
    if verification.status == "matched":
        return RunResult(
            status=RunStatus.APPLIED_PENDING_PROPAGATION,
            warnings=(_PROPAGATION_WARNING,),
            propagation_pending=True,
        )
    if mutation.status == "unchanged":
        return RunResult(status=RunStatus.FAILED_UNCHANGED, error_code="verification_mismatch")
    return RunResult(status=RunStatus.INDETERMINATE, error_code="verification_mismatch")
