"""Verified ownership-registry lifecycle updates."""

from typing import Protocol
from uuid import UUID

from compliance_agent.domain.ownership import (
    AddressListOwnershipRecord,
    OwnershipRecord,
    OwnershipRegistry,
)
from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.results import VerificationResult


class OwnershipRegistryStore(Protocol):
    """Validated ownership persistence boundary."""

    def load(self) -> OwnershipRegistry: ...

    def save(self, registry: OwnershipRegistry) -> None: ...


class OwnershipUpdate(FrozenModel):
    """Exact local evidence changes committed after successful UI verification."""

    added: tuple[UUID, ...] = ()
    removed: tuple[UUID, ...] = ()


class OwnershipLifecycleService:
    """Persist ownership evidence only after complete desired-state verification."""

    def __init__(self, store: OwnershipRegistryStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def commit_verified(  # noqa: C901, PLR0912 - one atomic registry transaction.
        self,
        change_set: ChangeSet,
        verification: VerificationResult,
    ) -> OwnershipUpdate:
        """Atomically add and remove local records proven by fresh UI state."""

        if verification.status != "matched":
            return OwnershipUpdate()
        if (
            verification.desired_state != change_set.expected_after
            or verification.observed_state != change_set.expected_after
        ):
            message = "ownership update verification does not match the approved expected state"
            raise OwnershipNotEstablished(message)
        registry = self._store.load()
        records = {record.ownership_id: record for record in registry.resources}
        address_list_records = {record.ownership_id: record for record in registry.address_lists}
        created_pairs, created_auxiliary = _created_resources(change_set)
        removed_ids = _fully_removed_ids(change_set)
        removed_auxiliary_ids = {
            item.ownership_id
            for item in change_set.address_lists_to_remove
            if item.ownership_id in address_list_records
        }
        added_ids: list[UUID] = []
        for ownership_id, rule_name, list_name, target_ou in created_pairs:
            proposed = OwnershipRecord(
                ownership_id=ownership_id,
                rule_display_name=rule_name,
                address_list_display_name=list_name,
                target_ou=target_ou,
                created_at=self._clock.now(),
            )
            existing = records.get(ownership_id)
            if existing is not None and not _same_resource_names(existing, proposed):
                message = f"local ownership evidence conflicts for newly verified {ownership_id}"
                raise OwnershipNotEstablished(message)
            if existing is None:
                records[ownership_id] = proposed
                added_ids.append(ownership_id)
        for ownership_id, display_name, target_ou in created_auxiliary:
            proposed_auxiliary = AddressListOwnershipRecord(
                ownership_id=ownership_id,
                display_name=display_name,
                target_ou=target_ou,
                purpose="bypass",
                created_at=self._clock.now(),
            )
            existing_auxiliary = address_list_records.get(ownership_id)
            if existing_auxiliary is not None and not _same_address_list_resource(
                existing_auxiliary, proposed_auxiliary
            ):
                message = (
                    "local ownership evidence conflicts for newly verified "
                    f"address list {ownership_id}"
                )
                raise OwnershipNotEstablished(message)
            if existing_auxiliary is None:
                address_list_records[ownership_id] = proposed_auxiliary
                added_ids.append(ownership_id)
        removed_existing = [ownership_id for ownership_id in removed_ids if ownership_id in records]
        for ownership_id in removed_existing:
            del records[ownership_id]
        removed_existing.extend(
            ownership_id
            for ownership_id in removed_auxiliary_ids
            if ownership_id in address_list_records
        )
        for ownership_id in removed_auxiliary_ids:
            address_list_records.pop(ownership_id, None)
        update = OwnershipUpdate(
            added=tuple(sorted(added_ids, key=str)),
            removed=tuple(sorted(removed_existing, key=str)),
        )
        if update.added or update.removed:
            self._store.save(
                OwnershipRegistry(
                    resources=tuple(
                        sorted(records.values(), key=lambda record: record.ownership_id.hex)
                    ),
                    address_lists=tuple(
                        sorted(
                            address_list_records.values(),
                            key=lambda record: record.ownership_id.hex,
                        )
                    ),
                    compliance_rules=registry.compliance_rules,
                )
            )
        return update


def _created_resources(
    change_set: ChangeSet,
) -> tuple[
    tuple[tuple[UUID, str, str, str], ...],
    tuple[tuple[UUID, str, str], ...],
]:
    rules = {rule.ownership_id: rule for rule in change_set.rules_to_create}
    address_lists = {
        address_list.ownership_id: address_list
        for address_list in change_set.address_lists_to_create
    }
    if not rules.keys() <= address_lists.keys():
        message = "verified ownership creation requires matching rule and address-list identities"
        raise OwnershipNotEstablished(message)
    pairs: list[tuple[UUID, str, str, str]] = []
    referenced_bypass_names: dict[str, str] = {}
    for ownership_id in sorted(rules, key=str):
        rule = rules[ownership_id]
        address_list = address_lists[ownership_id]
        if rule.address_list_names != (address_list.display_name,):
            message = f"newly verified ownership pair is not exact: {ownership_id}"
            raise OwnershipNotEstablished(message)
        pairs.append((ownership_id, rule.display_name, address_list.display_name, rule.target_ou))
        referenced_bypass_names.update(
            dict.fromkeys(rule.bypass_address_list_names, rule.target_ou)
        )
    auxiliary: list[tuple[UUID, str, str]] = []
    for ownership_id in sorted(address_lists.keys() - rules.keys(), key=str):
        address_list = address_lists[ownership_id]
        target_ou = referenced_bypass_names.get(address_list.display_name)
        if target_ou is None:
            message = "verified independent address-list creation must be referenced as a bypass"
            raise OwnershipNotEstablished(message)
        auxiliary.append((ownership_id, address_list.display_name, target_ou))
    return tuple(pairs), tuple(auxiliary)


def _fully_removed_ids(change_set: ChangeSet) -> tuple[UUID, ...]:
    removed_rules = {rule.ownership_id for rule in change_set.rules_to_remove}
    removed_lists = {
        address_list.ownership_id for address_list in change_set.address_lists_to_remove
    }
    allowed_auxiliary_names = {
        name for rule in change_set.rules_to_remove for name in rule.bypass_address_list_names
    }
    allowed_auxiliary_ids = {
        item.ownership_id
        for item in change_set.address_lists_to_remove
        if item.display_name in allowed_auxiliary_names
    }
    unexpected_lists = removed_lists - removed_rules - allowed_auxiliary_ids
    if unexpected_lists:
        message = "ownership address-list removal cannot leave its rule behind"
        raise OwnershipNotEstablished(message)
    return tuple(sorted(removed_rules & removed_lists, key=str))


def _same_resource_names(first: OwnershipRecord, second: OwnershipRecord) -> bool:
    return (
        first.rule_display_name == second.rule_display_name
        and first.address_list_display_name == second.address_list_display_name
        and first.target_ou == second.target_ou
    )


def _same_address_list_resource(
    first: AddressListOwnershipRecord,
    second: AddressListOwnershipRecord,
) -> bool:
    return (
        first.display_name == second.display_name
        and first.target_ou == second.target_ou
        and first.purpose == second.purpose
    )
