"""Fresh-read verification, timeout reconciliation, and authoritative status behavior."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from compliance_agent.domain.reconciliation import ReconciliationContext, reconcile_mutation
from compliance_agent.domain.reporting import determine_run_result
from compliance_agent.domain.verification import verify_state
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.results import MutationResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from tests.conftest import PREFIX, domain, owned_state


def _safe_retry_context(**updates: object) -> ReconciliationContext:
    values = {
        "retry_count": 0,
        "operation_is_idempotent": True,
        "ownership_confirmed": True,
        "root_ou_confirmed": True,
        "confirmation_valid": True,
    }
    values.update(updates)
    return ReconciliationContext.model_validate(values)


def test_verification_matches_complete_normalized_state() -> None:
    desired = owned_state(entries=(domain("example.com"),))

    result = verify_state(desired, desired)

    assert result.status == "matched"
    assert not result.differences


def test_verification_reports_missing_changed_unexpected_and_unmanaged_differences() -> None:
    desired = owned_state(entries=(domain("desired.example"),), unmanaged=("Manual",))
    changed = owned_state(entries=(domain("observed.example"),), unmanaged=("Other",))
    missing = verify_state(desired, BlockedSenderState(unmanaged_rule_names=("Other",)))
    mismatched = verify_state(desired, changed)

    assert missing.status == "target_missing"
    assert {difference.kind for difference in missing.differences} >= {"missing", "changed"}
    assert mismatched.status == "mismatched"
    assert any(difference.path.startswith("address_lists") for difference in mismatched.differences)


def test_duplicate_visible_rule_marker_is_never_selected() -> None:
    desired = owned_state()
    duplicate_id = uuid4()
    duplicate_rule = ManagedBlockedSenderRule(
        ownership_id=duplicate_id,
        display_name=desired.rules[0].display_name,
        address_list_names=(f"{PREFIX} Addresses {duplicate_id.hex[:8]}",),
    )
    duplicate_list = ManagedAddressList(
        ownership_id=duplicate_id,
        display_name=duplicate_rule.address_list_names[0],
    )
    observed = desired.model_copy(
        update={
            "rules": (*desired.rules, duplicate_rule),
            "address_lists": (*desired.address_lists, duplicate_list),
        }
    )

    result = verify_state(desired, observed)

    assert result.status == "duplicate_target"
    assert result.differences[0].kind == "duplicate"


def test_parser_failure_is_indeterminate_not_success() -> None:
    result = verify_state(owned_state(), None)

    assert result.status == "indeterminate"
    assert result.observed_state is None


def test_timeout_readback_present_continues_without_retry() -> None:
    before = owned_state(entries=(domain("old.example"),))
    desired = owned_state(entries=(domain("new.example"),))

    decision = reconcile_mutation(before, desired, desired, _safe_retry_context())

    assert decision.outcome == "desired_state_present"
    assert not decision.retry_is_safe


def test_timeout_readback_unchanged_allows_only_one_fully_safe_retry() -> None:
    before = owned_state(entries=(domain("old.example"),))
    desired = owned_state(entries=(domain("new.example"),))

    safe = reconcile_mutation(before, desired, before, _safe_retry_context())
    second_attempt = reconcile_mutation(
        before,
        desired,
        before,
        _safe_retry_context(retry_count=1),
    )
    ownership_lost = reconcile_mutation(
        before,
        desired,
        before,
        _safe_retry_context(ownership_confirmed=False),
    )

    assert safe.outcome == "mutation_not_applied"
    assert safe.retry_is_safe
    assert not second_attempt.retry_is_safe
    assert not ownership_lost.retry_is_safe


def test_partial_creation_is_reported_without_rollback_or_retry() -> None:
    before = BlockedSenderState()
    desired = owned_state(entries=(domain("new.example"),))
    observed = BlockedSenderState(address_lists=desired.address_lists)

    decision = reconcile_mutation(before, desired, observed, _safe_retry_context())

    assert decision.outcome == "partially_applied"
    assert not decision.retry_is_safe


def test_unrelated_state_change_or_missing_readback_is_indeterminate() -> None:
    before = owned_state(entries=(domain("old.example"),))
    desired = owned_state(entries=(domain("new.example"),))
    unrelated = owned_state(ownership_id=uuid4())

    changed = reconcile_mutation(before, desired, unrelated, _safe_retry_context())
    missing = reconcile_mutation(before, desired, None, _safe_retry_context())

    assert changed.outcome == "indeterminate"
    assert missing.outcome == "indeterminate"


def test_authoritative_report_distinguishes_ui_persistence_and_enforcement() -> None:
    desired = owned_state()
    verification = verify_state(desired, desired)
    mutation = MutationResult(status="completed", operation="save")

    result = determine_run_result(mutation, verification)

    assert result.status == RunStatus.APPLIED_PENDING_PROPAGATION
    assert result.propagation_pending
    assert "propagating" in result.warnings[0]


def test_authoritative_status_covers_terminal_failure_paths() -> None:
    mutation_partial = MutationResult(status="partial", operation="create", error_code="orphan")
    mutation_uncertain = MutationResult(status="uncertain", operation="save", error_code="timeout")
    mutation_unchanged = MutationResult(status="unchanged", operation="save")
    mismatch = verify_state(owned_state(), BlockedSenderState())

    assert determine_run_result(None, None, unsupported=True).status == RunStatus.UNSUPPORTED
    assert (
        determine_run_result(None, None, confirmation_rejected=True).status
        == RunStatus.CONFIRMATION_REJECTED
    )
    assert (
        determine_run_result(None, None, no_change_required=True).status
        == RunStatus.NO_CHANGE_REQUIRED
    )
    assert determine_run_result(None, None).status == RunStatus.FAILED_UNCHANGED
    assert determine_run_result(mutation_partial, None).status == RunStatus.PARTIALLY_APPLIED
    assert determine_run_result(mutation_uncertain, None).status == RunStatus.INDETERMINATE
    assert determine_run_result(mutation_unchanged, mismatch).status == RunStatus.FAILED_UNCHANGED
    assert determine_run_result(mutation_uncertain, mismatch).status == RunStatus.INDETERMINATE


def test_mutation_result_requires_consistent_error_evidence() -> None:
    with pytest.raises(ValidationError, match="requires an error code"):
        MutationResult(status="uncertain", operation="save")
    with pytest.raises(ValidationError, match="cannot include"):
        MutationResult(status="completed", operation="save", error_code="timeout")
