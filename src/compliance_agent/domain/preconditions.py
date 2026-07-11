"""Root-OU, confirmation-hash, and state-drift preconditions."""

from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.exceptions import RootOuNotConfirmed, StaleConfirmation
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.state import BlockedSenderState


def require_root_ou(target_ou: str) -> None:
    """Reject every organizational unit except the positively identified root."""

    if target_ou != "/":
        message = f"version 1 supports only the root OU, observed {target_ou!r}"
        raise RootOuNotConfirmed(message)


def validate_confirmation(
    approval: ConfirmationResponse,
    plan: TaskPlan,
    current_state: BlockedSenderState,
    change_set: ChangeSet,
) -> None:
    """Reject rejection responses, changed plans, changed state, or changed diffs."""

    require_root_ou(current_state.target_ou)
    expected = (canonical_hash(plan), canonical_hash(current_state), canonical_hash(change_set))
    supplied = (approval.plan_hash, approval.before_state_hash, approval.change_set_hash)
    if not approval.approved:
        message = "operator rejected the proposed mutation"
        raise StaleConfirmation(message)
    if supplied != expected:
        message = "confirmation hashes do not match the current plan, state, and change set"
        raise StaleConfirmation(message)


def state_has_drifted(confirmed_state_hash: str, observed_state: BlockedSenderState) -> bool:
    """Return whether a fresh read invalidates the previously confirmed state."""

    return confirmed_state_hash != canonical_hash(observed_state)
