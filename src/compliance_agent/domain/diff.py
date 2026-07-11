"""Pure current-to-desired state comparison."""

from collections.abc import Callable
from uuid import UUID

from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.state import BlockedSenderState


def calculate_change_set(
    current_state: BlockedSenderState,
    desired_state: BlockedSenderState,
) -> ChangeSet:
    """Return exact create, update, and remove collections without side effects."""

    rule_changes = _partition(
        current_state.rules,
        desired_state.rules,
        lambda item: item.ownership_id,
    )
    list_changes = _partition(
        current_state.address_lists,
        desired_state.address_lists,
        lambda item: item.ownership_id,
    )
    return ChangeSet(
        before_state=current_state,
        expected_after=desired_state,
        rules_to_create=rule_changes[0],
        rules_to_update=rule_changes[1],
        rules_to_remove=rule_changes[2],
        address_lists_to_create=list_changes[0],
        address_lists_to_update=list_changes[1],
        address_lists_to_remove=list_changes[2],
    )


def _partition[Resource: (ManagedBlockedSenderRule, ManagedAddressList)](
    current: tuple[Resource, ...],
    desired: tuple[Resource, ...],
    identity: Callable[[Resource], UUID],
) -> tuple[tuple[Resource, ...], tuple[Resource, ...], tuple[Resource, ...]]:
    current_by_id = {identity(item): item for item in current}
    desired_by_id = {identity(item): item for item in desired}
    created = tuple(
        desired_by_id[key] for key in sorted(desired_by_id.keys() - current_by_id, key=str)
    )
    removed = tuple(
        current_by_id[key] for key in sorted(current_by_id.keys() - desired_by_id, key=str)
    )
    updated = tuple(
        desired_by_id[key]
        for key in sorted(current_by_id.keys() & desired_by_id, key=str)
        if desired_by_id[key] != current_by_id[key]
    )
    return created, updated, removed
