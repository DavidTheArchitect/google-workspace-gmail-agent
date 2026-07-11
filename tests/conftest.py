"""Focused domain builders shared by behavior tests."""

from collections.abc import Iterable
from uuid import UUID

from compliance_agent.domain.ownership import OwnershipRecord, OwnershipRegistry
from compliance_agent.schemas.resources import (
    AddressEntry,
    ManagedAddressList,
    ManagedBlockedSenderRule,
)
from compliance_agent.schemas.state import BlockedSenderState

OWNERSHIP_ID = UUID("8f3c82a1-2d36-42cd-ae97-85ee319bb21d")
SECOND_ID = UUID("1a3c82a1-2d36-42cd-ae97-85ee319bb21d")
PREFIX = "[Compliance Agent]"


def domain(value: str) -> AddressEntry:
    """Build one validated domain entry."""

    return AddressEntry(kind="domain", value=value)


def email(value: str) -> AddressEntry:
    """Build one validated email entry."""

    return AddressEntry(kind="email", value=value)


def owned_state(
    *,
    ownership_id: UUID = OWNERSHIP_ID,
    notice: str | None = "Mail rejected.",
    entries: Iterable[AddressEntry] = (),
    unmanaged: tuple[str, ...] = (),
) -> BlockedSenderState:
    """Build one correctly related managed rule/list pair."""

    short_id = ownership_id.hex[:8]
    rule_name = f"{PREFIX} Block rule {short_id}"
    list_name = f"{PREFIX} Addresses {short_id}"
    return BlockedSenderState(
        rules=(
            ManagedBlockedSenderRule(
                ownership_id=ownership_id,
                display_name=rule_name,
                address_list_names=(list_name,),
                rejection_notice=notice,
            ),
        ),
        address_lists=(
            ManagedAddressList(
                ownership_id=ownership_id,
                display_name=list_name,
                entries=tuple(entries),
            ),
        ),
        unmanaged_rule_names=unmanaged,
    )


def registry_for(
    ownership_id: UUID = OWNERSHIP_ID,
    *,
    rule_name: str | None = None,
    list_name: str | None = None,
) -> OwnershipRegistry:
    """Build matching local ownership evidence."""

    short_id = ownership_id.hex[:8]
    return OwnershipRegistry(
        resources=(
            OwnershipRecord(
                ownership_id=ownership_id,
                rule_display_name=rule_name or f"{PREFIX} Block rule {short_id}",
                address_list_display_name=list_name or f"{PREFIX} Addresses {short_id}",
                created_at="2026-07-10T18:30:00Z",
            ),
        )
    )
