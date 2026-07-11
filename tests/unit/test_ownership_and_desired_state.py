"""Dual ownership evidence and deterministic desired-state behavior."""

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from compliance_agent.domain.desired_state import calculate_desired_state
from compliance_agent.domain.ownership import (
    OwnershipRecord,
    OwnershipRegistry,
    require_owned_address_list,
    require_owned_rule,
)
from compliance_agent.exceptions import AmbiguousTarget, OwnershipNotEstablished
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.state import BlockedSenderState
from tests.conftest import (
    OWNERSHIP_ID,
    PREFIX,
    SECOND_ID,
    domain,
    owned_state,
    registry_for,
)


def test_both_visible_and_local_evidence_establish_ownership() -> None:
    state = owned_state(entries=(domain("old.example"),))
    registry = registry_for()

    assert require_owned_rule(state.rules[0], registry, PREFIX).ownership_id == OWNERSHIP_ID
    assert (
        require_owned_address_list(state.address_lists[0], registry, PREFIX).ownership_id
        == OWNERSHIP_ID
    )


def test_missing_or_contradictory_ownership_evidence_is_read_only() -> None:
    state = owned_state()
    with pytest.raises(OwnershipNotEstablished):
        require_owned_rule(state.rules[0], OwnershipRegistry(), PREFIX)
    with pytest.raises(OwnershipNotEstablished):
        require_owned_address_list(
            state.address_lists[0],
            registry_for(list_name="[Compliance Agent] Addresses different"),
            PREFIX,
        )


def test_duplicate_local_ownership_records_are_rejected() -> None:
    record = registry_for().resources[0]
    with pytest.raises(ValidationError, match="duplicate ownership"):
        OwnershipRegistry(resources=(record, record))


def test_add_to_exact_owned_rule_is_idempotent_and_sorted() -> None:
    state = owned_state(entries=(domain("z.example"), domain("old.example")))
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "entries": [
                        {"kind": "domain", "value": "old.example"},
                        {"kind": "domain", "value": "a.example"},
                    ],
                }
            ],
        }
    )

    result = calculate_desired_state(state, plan, registry_for(), (), PREFIX)

    assert [entry.normalized_value for entry in result.desired_state.address_lists[0].entries] == [
        "a.example",
        "old.example",
        "z.example",
    ]


def test_different_notice_creates_separate_rule_without_changing_existing_notice() -> None:
    state = owned_state(notice="Existing notice", entries=(domain("old.example"),))
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "rejection_notice": "Separate notice",
                    "entries": [{"kind": "domain", "value": "new.example"}],
                }
            ],
        }
    )

    result = calculate_desired_state(state, plan, registry_for(), (SECOND_ID,), PREFIX)

    rules = {rule.ownership_id: rule for rule in result.desired_state.rules}
    assert rules[OWNERSHIP_ID].rejection_notice == "Existing notice"
    assert rules[SECOND_ID].rejection_notice == "Separate notice"


def test_add_without_matching_rule_creates_owned_pair_from_injected_id() -> None:
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "entries": [{"kind": "domain", "value": "new.example"}],
                }
            ],
        }
    )

    result = calculate_desired_state(
        BlockedSenderState(),
        plan,
        OwnershipRegistry(),
        (SECOND_ID,),
        PREFIX,
    )

    assert result.desired_state.rules[0].ownership_id == SECOND_ID
    assert result.desired_state.address_lists[0].ownership_id == SECOND_ID
    assert result.desired_state.rules[0].address_list_names == (
        result.desired_state.address_lists[0].display_name,
    )


def test_new_rule_fails_deterministically_without_injected_identifier() -> None:
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "create_blocked_sender_rule",
                    "entries": [{"kind": "domain", "value": "new.example"}],
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="another injected"):
        calculate_desired_state(BlockedSenderState(), plan, OwnershipRegistry(), (), PREFIX)


def test_new_rule_rejects_short_id_display_name_collision() -> None:
    state = owned_state(notice="Existing")
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "rejection_notice": "Different",
                    "entries": [{"kind": "domain", "value": "new.example"}],
                }
            ],
        }
    )
    colliding_id = UUID(f"{OWNERSHIP_ID.hex[:8]}-0000-4000-8000-000000000000")

    with pytest.raises(AmbiguousTarget, match="display names"):
        calculate_desired_state(state, plan, registry_for(), (colliding_id,), PREFIX)


def test_ambiguous_matching_rules_require_explicit_target() -> None:
    first = owned_state(ownership_id=OWNERSHIP_ID, notice=None)
    second = owned_state(ownership_id=SECOND_ID, notice=None)
    state = BlockedSenderState(
        rules=first.rules + second.rules,
        address_lists=first.address_lists + second.address_lists,
    )
    registry = OwnershipRegistry(
        resources=registry_for(OWNERSHIP_ID).resources + registry_for(SECOND_ID).resources
    )
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "entries": [{"kind": "domain", "value": "new.example"}],
                }
            ],
        }
    )

    with pytest.raises(AmbiguousTarget, match="several owned rules"):
        calculate_desired_state(state, plan, registry, (uuid4(),), PREFIX)


def test_remove_entry_and_set_notice_report_rule_wide_impact() -> None:
    state = owned_state(entries=(domain("one.example"), domain("two.example")))
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "remove_blocked_entries",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "entries": [{"kind": "domain", "value": "one.example"}],
                },
                {
                    "type": "set_rejection_notice",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "rejection_notice": "New notice",
                },
            ],
        }
    )

    result = calculate_desired_state(state, plan, registry_for(), (), PREFIX)

    assert result.notice_affected_entry_count == 1
    assert result.desired_state.rules[0].rejection_notice == "New notice"
    assert result.desired_state.address_lists[0].entries == (domain("two.example"),)


def test_remove_rule_can_leave_or_safely_remove_its_owned_list() -> None:
    state = owned_state()
    leave_list = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {"type": "remove_blocked_sender_rule", "target_rule_id": str(OWNERSHIP_ID)}
            ],
        }
    )
    remove_list = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "remove_blocked_sender_rule",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "remove_owned_address_list": True,
                }
            ],
        }
    )

    first = calculate_desired_state(state, leave_list, registry_for(), (), PREFIX)
    second = calculate_desired_state(state, remove_list, registry_for(), (), PREFIX)

    assert not first.desired_state.rules
    assert first.desired_state.address_lists
    assert not second.desired_state.rules
    assert not second.desired_state.address_lists


def test_incorrect_rule_to_list_association_is_rejected() -> None:
    state = owned_state()
    wrong_rule = state.rules[0].model_copy(update={"address_list_names": ("wrong list",)})
    invalid_state = state.model_copy(update={"rules": (wrong_rule,)})
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "remove_blocked_entries",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "entries": [{"kind": "domain", "value": "absent.example"}],
                }
            ],
        }
    )

    with pytest.raises(AmbiguousTarget, match="relationship"):
        calculate_desired_state(invalid_state, plan, registry_for(), (), PREFIX)


def test_remove_referenced_list_is_rejected() -> None:
    state = owned_state()
    other_rule = ManagedBlockedSenderRule(
        ownership_id=SECOND_ID,
        display_name=f"{PREFIX} Block rule {SECOND_ID.hex[:8]}",
        address_list_names=(state.address_lists[0].display_name,),
    )
    linked_state = state.model_copy(update={"rules": (*state.rules, other_rule)})
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "remove_blocked_sender_rule",
                    "target_rule_id": str(OWNERSHIP_ID),
                    "remove_owned_address_list": True,
                }
            ],
        }
    )

    with pytest.raises(AmbiguousTarget, match="remains referenced"):
        calculate_desired_state(linked_state, plan, registry_for(), (), PREFIX)


def test_registry_records_do_not_make_unmarked_resources_owned() -> None:
    rule = ManagedBlockedSenderRule(
        ownership_id=OWNERSHIP_ID,
        display_name="Manual rule",
        address_list_names=("Manual list",),
    )
    address_list = ManagedAddressList(
        ownership_id=OWNERSHIP_ID,
        display_name="Manual list",
    )
    registry = OwnershipRegistry(
        resources=(
            OwnershipRecord(
                ownership_id=OWNERSHIP_ID,
                rule_display_name="Manual rule",
                address_list_display_name="Manual list",
                created_at="2026-07-10T18:30:00Z",
            ),
        )
    )

    with pytest.raises(OwnershipNotEstablished):
        require_owned_rule(rule, registry, PREFIX)
    with pytest.raises(OwnershipNotEstablished):
        require_owned_address_list(address_list, registry, PREFIX)
