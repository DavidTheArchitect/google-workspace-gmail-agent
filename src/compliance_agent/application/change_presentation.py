"""Pure presentation projections over an exact change set."""

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.resources import AddressEntry


class AddressListDelta(FrozenModel):
    """Entries added to and removed from one updated address list."""

    added: tuple[AddressEntry, ...] = ()
    removed: tuple[AddressEntry, ...] = ()


def address_list_deltas(change_set: ChangeSet) -> dict[str, AddressListDelta]:
    """Per-updated-list entry differences keyed by ownership ID hex.

    Lists are matched between the before-state and the updates by ownership ID,
    which state validation keeps unique. Entries compare on normalized value;
    updated-list order is preserved for additions, before-state order for
    removals.
    """

    before = {item.ownership_id: item for item in change_set.before_state.address_lists}
    deltas: dict[str, AddressListDelta] = {}
    for updated in change_set.address_lists_to_update:
        previous = before.get(updated.ownership_id)
        previous_entries = previous.entries if previous is not None else ()
        previous_keys = {entry.normalized_value for entry in previous_entries}
        updated_keys = {entry.normalized_value for entry in updated.entries}
        deltas[updated.ownership_id.hex] = AddressListDelta(
            added=tuple(
                entry for entry in updated.entries if entry.normalized_value not in previous_keys
            ),
            removed=tuple(
                entry for entry in previous_entries if entry.normalized_value not in updated_keys
            ),
        )
    return deltas
