"""Dual-evidence ownership policy for application-managed resources."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule


class OwnershipRecord(FrozenModel):
    """Local evidence paired with visible resource markers in the Admin console."""

    ownership_id: UUID
    rule_display_name: str = Field(min_length=1)
    address_list_display_name: str = Field(min_length=1)
    target_ou: Literal["/"] = "/"
    created_at: str


class OwnershipRegistry(FrozenModel):
    """Versioned local ownership records."""

    schema_version: Literal["1.0"] = "1.0"
    resources: tuple[OwnershipRecord, ...] = ()

    def find(self, ownership_id: UUID) -> OwnershipRecord | None:
        """Return the exact record, rejecting duplicate local identities."""

        matches = [record for record in self.resources if record.ownership_id == ownership_id]
        if len(matches) > 1:
            message = f"duplicate local ownership records for {ownership_id}"
            raise OwnershipNotEstablished(message)
        return matches[0] if matches else None


def managed_resource_names(prefix: str, ownership_id: UUID) -> tuple[str, str]:
    """Create predictable visible names without weakening UUID ownership identity."""

    short_id = ownership_id.hex[:8]
    return (f"{prefix} Block rule {short_id}", f"{prefix} Addresses {short_id}")


def require_owned_rule(
    rule: ManagedBlockedSenderRule,
    registry: OwnershipRegistry,
    prefix: str,
) -> OwnershipRecord:
    """Require exact visible marker, local record, names, ID, and root OU evidence."""

    record = registry.find(rule.ownership_id)
    is_visible = rule.display_name.startswith(f"{prefix} ")
    if (
        record is None
        or not is_visible
        or record.rule_display_name != rule.display_name
        or record.target_ou != rule.target_ou
    ):
        message = f"ownership is not established for rule {rule.ownership_id}"
        raise OwnershipNotEstablished(message)
    return record


def require_owned_address_list(
    address_list: ManagedAddressList,
    registry: OwnershipRegistry,
    prefix: str,
) -> OwnershipRecord:
    """Require exact visible marker and matching local address-list evidence."""

    record = registry.find(address_list.ownership_id)
    is_visible = address_list.display_name.startswith(f"{prefix} ")
    if (
        record is None
        or not is_visible
        or record.address_list_display_name != address_list.display_name
    ):
        message = f"ownership is not established for address list {address_list.ownership_id}"
        raise OwnershipNotEstablished(message)
    return record
