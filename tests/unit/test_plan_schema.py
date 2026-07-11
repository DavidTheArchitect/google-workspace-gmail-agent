"""TaskPlan invariants at the LLM security boundary."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.resources import (
    AddressEntry,
    ManagedAddressList,
    ManagedBlockedSenderRule,
)


def test_plan_calculates_normalized_entry_instead_of_trusting_model_output() -> None:
    plan = TaskPlan.model_validate(
        {
            "status": "plan",
            "actions": [
                {
                    "type": "add_blocked_entries",
                    "entries": [{"kind": "domain", "value": "Example.COM"}],
                }
            ],
        }
    )

    entry = plan.actions[0].entries[0]  # type: ignore[union-attr]
    assert entry.normalized_value == "example.com"


def test_forged_normalized_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        AddressEntry(kind="domain", value="example.com", normalized_value="different.com")


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "plan"},
        {"status": "clarification_needed"},
        {"status": "unsupported"},
        {
            "status": "clarification_needed",
            "clarification_question": "Which domain?",
            "actions": [{"type": "list_blocked_sender_rules"}],
        },
        {
            "status": "unsupported",
            "unsupported_reason": "Child OU",
            "actions": [{"type": "list_blocked_sender_rules"}],
        },
    ],
)
def test_terminal_status_and_action_invariants_reject_invalid_combinations(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(payload)


def test_duplicate_normalized_entries_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate normalized"):
        TaskPlan.model_validate(
            {
                "status": "plan",
                "actions": [
                    {
                        "type": "add_blocked_entries",
                        "entries": [
                            {"kind": "domain", "value": "EXAMPLE.com"},
                            {"kind": "domain", "value": "example.com"},
                        ],
                    }
                ],
            }
        )


def test_read_only_list_action_cannot_be_mixed_with_mutations() -> None:
    with pytest.raises(ValidationError, match="only action"):
        TaskPlan.model_validate(
            {
                "status": "plan",
                "actions": [
                    {"type": "list_blocked_sender_rules"},
                    {
                        "type": "add_blocked_entries",
                        "entries": [{"kind": "domain", "value": "example.com"}],
                    },
                ],
            }
        )


def test_empty_create_and_blank_notices_are_rejected() -> None:
    with pytest.raises(ValidationError, match="requires at least one entry"):
        TaskPlan.model_validate(
            {
                "status": "plan",
                "actions": [{"type": "create_blocked_sender_rule", "entries": []}],
            }
        )
    with pytest.raises(ValidationError, match="cannot be blank"):
        TaskPlan.model_validate(
            {
                "status": "plan",
                "actions": [
                    {
                        "type": "set_rejection_notice",
                        "target_rule_id": str(uuid4()),
                        "rejection_notice": "   ",
                    }
                ],
            }
        )


def test_managed_rule_requires_list_and_nonblank_notice() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ManagedBlockedSenderRule(
            ownership_id=uuid4(),
            display_name="rule",
            address_list_names=(),
        )


def test_resource_models_reject_duplicate_entries_and_list_names() -> None:
    entry = AddressEntry(kind="domain", value="example.com")
    with pytest.raises(ValidationError, match="duplicate normalized"):
        ManagedAddressList(
            ownership_id=uuid4(),
            display_name="list",
            entries=(entry, entry),
        )
    with pytest.raises(ValidationError, match="duplicate address-list"):
        ManagedBlockedSenderRule(
            ownership_id=uuid4(),
            display_name="rule",
            address_list_names=("list", "list"),
        )
    with pytest.raises(ValidationError, match="cannot be blank"):
        ManagedBlockedSenderRule(
            ownership_id=uuid4(),
            display_name="rule",
            address_list_names=("list",),
            rejection_notice=" ",
        )
