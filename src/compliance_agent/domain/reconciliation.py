"""Fail-closed reconciliation after a mutation response becomes uncertain."""

from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.verification import verify_state
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.results import ReconciliationDecision
from compliance_agent.schemas.state import BlockedSenderState


class ReconciliationContext(FrozenModel):
    """All deterministic safety evidence needed to authorize one retry."""

    retry_count: int = 0
    operation_is_idempotent: bool
    ownership_confirmed: bool
    root_ou_confirmed: bool
    confirmation_valid: bool


def reconcile_mutation(
    before_state: BlockedSenderState,
    desired_state: BlockedSenderState,
    observed_state: BlockedSenderState | None,
    context: ReconciliationContext,
) -> ReconciliationDecision:
    """Classify read-back and authorize at most one proven-safe retry."""

    if observed_state is None:
        return ReconciliationDecision(
            outcome="indeterminate",
            retry_is_safe=False,
            observed_state=None,
            explanation_code="read_back_unavailable",
        )
    verification = verify_state(desired_state, observed_state)
    if verification.status == "matched":
        return ReconciliationDecision(
            outcome="desired_state_present",
            retry_is_safe=False,
            observed_state=observed_state,
            explanation_code="mutation_present_after_read_back",
        )
    if canonical_hash(before_state) == canonical_hash(observed_state):
        return ReconciliationDecision(
            outcome="mutation_not_applied",
            retry_is_safe=_retry_preconditions_hold(context),
            observed_state=observed_state,
            explanation_code="before_state_unchanged",
        )
    if _has_any_desired_change(before_state, desired_state, observed_state):
        return ReconciliationDecision(
            outcome="partially_applied",
            retry_is_safe=False,
            observed_state=observed_state,
            explanation_code="some_but_not_all_desired_resources_present",
        )
    return ReconciliationDecision(
        outcome="indeterminate",
        retry_is_safe=False,
        observed_state=observed_state,
        explanation_code="state_changed_outside_expected_mutation",
    )


def _retry_preconditions_hold(context: ReconciliationContext) -> bool:
    return (
        context.retry_count == 0
        and context.operation_is_idempotent
        and context.ownership_confirmed
        and context.root_ou_confirmed
        and context.confirmation_valid
    )


def _has_any_desired_change(
    before: BlockedSenderState,
    desired: BlockedSenderState,
    observed: BlockedSenderState,
) -> bool:
    before_resources = _resource_json(before)
    desired_resources = _resource_json(desired)
    observed_resources = _resource_json(observed)
    changed_keys = {
        key
        for key in before_resources.keys() | desired_resources.keys()
        if before_resources.get(key) != desired_resources.get(key)
    }
    return any(observed_resources.get(key) == desired_resources.get(key) for key in changed_keys)


def _resource_json(state: BlockedSenderState) -> dict[str, str]:
    resources = {f"rule:{rule.ownership_id}": rule.model_dump_json() for rule in state.rules}
    resources.update(
        {
            f"list:{address_list.ownership_id}": address_list.model_dump_json()
            for address_list in state.address_lists
        }
    )
    return resources
