"""Deterministic diff, canonical hashing, and stale-confirmation checks."""

from uuid import uuid4

import pytest

from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.hashing import canonical_hash, canonical_json
from compliance_agent.domain.preconditions import (
    require_root_ou,
    state_has_drifted,
    validate_confirmation,
)
from compliance_agent.exceptions import RootOuNotConfirmed, StaleConfirmation
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.state import BlockedSenderState
from tests.conftest import domain, owned_state


def test_diff_partitions_create_update_remove_and_noop() -> None:
    current = owned_state(entries=(domain("old.example"),))
    updated = owned_state(entries=(domain("new.example"),))
    change_set = calculate_change_set(current, updated)

    assert change_set.has_mutations
    assert change_set.rules_to_update == ()
    assert change_set.address_lists_to_update == updated.address_lists
    assert not calculate_change_set(current, current).has_mutations

    created = calculate_change_set(BlockedSenderState(), current)
    removed = calculate_change_set(current, BlockedSenderState())
    assert created.rules_to_create == current.rules
    assert removed.address_lists_to_remove == current.address_lists


def test_canonical_hash_ignores_collection_order_and_normalizes_unicode() -> None:
    first = owned_state(
        entries=(domain("z.example"), domain("a.example")),
        unmanaged=("Z", "e\u0301"),
    )
    second = owned_state(
        entries=(domain("a.example"), domain("z.example")),
        unmanaged=("é", "Z"),
    )

    assert canonical_hash(first) == canonical_hash(second)
    assert canonical_json(first) == canonical_json(second)


def test_confirmation_accepts_exact_current_hashes() -> None:
    state = owned_state()
    plan = TaskPlan(status="plan", actions=({"type": "list_blocked_sender_rules"},))
    change_set = calculate_change_set(state, state)
    approval = ConfirmationResponse(
        approved=True,
        approval_id="approval-1",
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(state),
        change_set_hash=canonical_hash(change_set),
    )

    validate_confirmation(approval, plan, state, change_set)
    assert not state_has_drifted(approval.before_state_hash, state)


def test_rejected_or_stale_confirmation_cannot_authorize_mutation() -> None:
    state = owned_state()
    plan = TaskPlan(status="plan", actions=({"type": "list_blocked_sender_rules"},))
    change_set = calculate_change_set(state, state)
    stale = ConfirmationResponse(
        approved=True,
        approval_id="approval-1",
        plan_hash="0" * 64,
        before_state_hash=canonical_hash(state),
        change_set_hash=canonical_hash(change_set),
    )
    rejected = stale.model_copy(update={"approved": False, "plan_hash": canonical_hash(plan)})

    with pytest.raises(StaleConfirmation, match="do not match"):
        validate_confirmation(stale, plan, state, change_set)
    with pytest.raises(StaleConfirmation, match="rejected"):
        validate_confirmation(rejected, plan, state, change_set)
    assert state_has_drifted(canonical_hash(state), owned_state(ownership_id=uuid4()))


def test_root_ou_check_fails_closed() -> None:
    require_root_ou("/")
    with pytest.raises(RootOuNotConfirmed):
        require_root_ou("/Sales")
