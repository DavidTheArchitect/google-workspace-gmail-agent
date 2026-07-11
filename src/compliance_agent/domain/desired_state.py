"""Pure translation from validated actions to an expected root-OU configuration."""

from collections.abc import Iterator
from uuid import UUID

from compliance_agent.domain.ownership import (
    OwnershipRegistry,
    managed_resource_names,
    require_owned_address_list,
    require_owned_rule,
)
from compliance_agent.exceptions import AmbiguousTarget
from compliance_agent.schemas.changes import DesiredStateResult
from compliance_agent.schemas.plan import (
    AddBlockedEntries,
    CreateBlockedSenderRule,
    ListBlockedSenderRules,
    RemoveBlockedEntries,
    RemoveBlockedSenderRule,
    SetRejectionNotice,
    TaskPlan,
)
from compliance_agent.schemas.resources import (
    AddressEntry,
    ManagedAddressList,
    ManagedBlockedSenderRule,
)
from compliance_agent.schemas.state import BlockedSenderState


def calculate_desired_state(
    current_state: BlockedSenderState,
    plan: TaskPlan,
    ownership_registry: OwnershipRegistry,
    new_ownership_ids: tuple[UUID, ...],
    managed_prefix: str,
) -> DesiredStateResult:
    """Apply a plan without I/O, time, or randomness.

    New ownership IDs are injected so retries and tests produce identical states. Existing
    resources are modified only after both visible and local ownership evidence is established.
    """

    rules = {rule.ownership_id: rule for rule in current_state.rules}
    address_lists = {item.ownership_id: item for item in current_state.address_lists}
    identifiers = iter(new_ownership_ids)
    affected_entry_count = 0

    for action in plan.actions:
        if isinstance(action, (CreateBlockedSenderRule, AddBlockedEntries)):
            affected_entry_count += _apply_create_or_add(
                action,
                rules,
                address_lists,
                ownership_registry,
                identifiers,
                managed_prefix,
            )
        elif isinstance(action, RemoveBlockedEntries):
            _remove_entries(
                action,
                rules,
                address_lists,
                ownership_registry,
                managed_prefix,
            )
        elif isinstance(action, SetRejectionNotice):
            affected_entry_count += _set_notice(
                action,
                rules,
                address_lists,
                ownership_registry,
                managed_prefix,
            )
        elif isinstance(action, RemoveBlockedSenderRule):
            _remove_rule(
                action,
                rules,
                address_lists,
                ownership_registry,
                managed_prefix,
            )
        elif isinstance(action, ListBlockedSenderRules):
            continue

    _require_unique_display_names(rules, address_lists, current_state.unmanaged_rule_names)
    desired = BlockedSenderState(
        rules=tuple(sorted(rules.values(), key=lambda rule: rule.ownership_id.hex)),
        address_lists=tuple(
            sorted(address_lists.values(), key=lambda address_list: address_list.ownership_id.hex)
        ),
        unmanaged_rule_names=tuple(sorted(current_state.unmanaged_rule_names)),
    )
    return DesiredStateResult(
        desired_state=desired,
        notice_affected_entry_count=affected_entry_count,
    )


def _apply_create_or_add(  # noqa: PLR0913 - explicit working sets keep policy reviewable.
    action: CreateBlockedSenderRule | AddBlockedEntries,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    identifiers: Iterator[UUID],
    prefix: str,
) -> int:
    if isinstance(action, CreateBlockedSenderRule):
        _create_rule(
            action.entries,
            action.rejection_notice,
            rules,
            address_lists,
            identifiers,
            prefix,
        )
        return 0

    target = _resolve_add_target(action, rules, address_lists, registry, prefix)
    if target is None:
        _create_rule(
            action.entries,
            action.rejection_notice,
            rules,
            address_lists,
            identifiers,
            prefix,
        )
        return 0

    target_rule, target_list = target
    if (
        action.rejection_notice is not None
        and action.rejection_notice != target_rule.rejection_notice
    ):
        _create_rule(
            action.entries,
            action.rejection_notice,
            rules,
            address_lists,
            identifiers,
            prefix,
        )
        return 0

    existing = {entry.normalized_value for entry in target_list.entries}
    combined = target_list.entries + tuple(
        entry for entry in action.entries if entry.normalized_value not in existing
    )
    address_lists[target_list.ownership_id] = target_list.model_copy(
        update={"entries": _sorted_entries(combined)}
    )
    return 0


def _resolve_add_target(
    action: AddBlockedEntries,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    prefix: str,
) -> tuple[ManagedBlockedSenderRule, ManagedAddressList] | None:
    if action.target_rule_id is not None:
        rule = _require_rule(action.target_rule_id, rules)
        require_owned_rule(rule, registry, prefix)
        return rule, _require_rule_list(rule, address_lists, registry, prefix)

    candidates: list[tuple[ManagedBlockedSenderRule, ManagedAddressList]] = []
    for rule in rules.values():
        if rule.rejection_notice != action.rejection_notice:
            continue
        require_owned_rule(rule, registry, prefix)
        candidates.append((rule, _require_rule_list(rule, address_lists, registry, prefix)))
    if len(candidates) > 1:
        message = "several owned rules have the requested notice; target_rule_id is required"
        raise AmbiguousTarget(message)
    return candidates[0] if candidates else None


def _create_rule(  # noqa: PLR0913 - construction inputs are distinct domain concepts.
    entries: tuple[AddressEntry, ...],
    notice: str | None,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    identifiers: Iterator[UUID],
    prefix: str,
) -> None:
    try:
        ownership_id = next(identifiers)
    except StopIteration as error:
        message = "desired state needs another injected ownership ID"
        raise ValueError(message) from error
    if ownership_id in rules or ownership_id in address_lists:
        message = f"ownership ID already exists: {ownership_id}"
        raise ValueError(message)
    rule_name, list_name = managed_resource_names(prefix, ownership_id)
    address_lists[ownership_id] = ManagedAddressList(
        ownership_id=ownership_id,
        display_name=list_name,
        entries=_sorted_entries(entries),
    )
    rules[ownership_id] = ManagedBlockedSenderRule(
        ownership_id=ownership_id,
        display_name=rule_name,
        address_list_names=(list_name,),
        rejection_notice=notice,
    )


def _remove_entries(
    action: RemoveBlockedEntries,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    prefix: str,
) -> None:
    rule = _require_rule(action.target_rule_id, rules)
    require_owned_rule(rule, registry, prefix)
    address_list = _require_rule_list(rule, address_lists, registry, prefix)
    removed = {entry.normalized_value for entry in action.entries}
    remaining = tuple(
        entry for entry in address_list.entries if entry.normalized_value not in removed
    )
    address_lists[address_list.ownership_id] = address_list.model_copy(
        update={"entries": _sorted_entries(remaining)}
    )


def _set_notice(
    action: SetRejectionNotice,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    prefix: str,
) -> int:
    rule = _require_rule(action.target_rule_id, rules)
    require_owned_rule(rule, registry, prefix)
    address_list = _require_rule_list(rule, address_lists, registry, prefix)
    if rule.rejection_notice == action.rejection_notice:
        return 0
    rules[rule.ownership_id] = rule.model_copy(update={"rejection_notice": action.rejection_notice})
    return len(address_list.entries)


def _remove_rule(
    action: RemoveBlockedSenderRule,
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    prefix: str,
) -> None:
    rule = _require_rule(action.target_rule_id, rules)
    require_owned_rule(rule, registry, prefix)
    address_list = _require_rule_list(rule, address_lists, registry, prefix)
    del rules[rule.ownership_id]
    if not action.remove_owned_address_list:
        return
    if any(address_list.display_name in item.address_list_names for item in rules.values()):
        message = "owned address list remains referenced by another rule"
        raise AmbiguousTarget(message)
    del address_lists[address_list.ownership_id]


def _require_rule(
    ownership_id: UUID,
    rules: dict[UUID, ManagedBlockedSenderRule],
) -> ManagedBlockedSenderRule:
    try:
        return rules[ownership_id]
    except KeyError as error:
        message = f"managed rule was not observed: {ownership_id}"
        raise AmbiguousTarget(message) from error


def _require_rule_list(
    rule: ManagedBlockedSenderRule,
    address_lists: dict[UUID, ManagedAddressList],
    registry: OwnershipRegistry,
    prefix: str,
) -> ManagedAddressList:
    if len(rule.address_list_names) != 1:
        message = f"owned rule must reference exactly one owned list: {rule.ownership_id}"
        raise AmbiguousTarget(message)
    matches = [
        item for item in address_lists.values() if item.display_name == rule.address_list_names[0]
    ]
    if len(matches) != 1 or matches[0].ownership_id != rule.ownership_id:
        message = f"owned rule/list relationship is missing or ambiguous: {rule.ownership_id}"
        raise AmbiguousTarget(message)
    require_owned_address_list(matches[0], registry, prefix)
    return matches[0]


def _sorted_entries(entries: tuple[AddressEntry, ...]) -> tuple[AddressEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: (entry.normalized_value, entry.kind)))


def _require_unique_display_names(
    rules: dict[UUID, ManagedBlockedSenderRule],
    address_lists: dict[UUID, ManagedAddressList],
    unmanaged_rule_names: tuple[str, ...],
) -> None:
    rule_names = [rule.display_name for rule in rules.values()] + list(unmanaged_rule_names)
    if len(rule_names) != len(set(rule_names)):
        message = "blocked-sender rule display names are ambiguous"
        raise AmbiguousTarget(message)
    list_names = [address_list.display_name for address_list in address_lists.values()]
    if len(list_names) != len(set(list_names)):
        message = "address-list display names are ambiguous"
        raise AmbiguousTarget(message)
