"""Dual-evidence ownership policy for application-managed resources."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule


class OwnershipRecord(FrozenModel):
    """Local evidence paired with visible resource markers in the Admin console."""

    ownership_id: UUID
    rule_display_name: str = Field(min_length=1)
    address_list_display_name: str = Field(min_length=1)
    target_ou: Literal["/"] = "/"
    created_at: datetime

    @model_validator(mode="after")
    def require_aware_creation_time(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            message = "ownership creation time must be timezone-aware"
            raise ValueError(message)
        return self


class OwnershipRegistry(FrozenModel):
    """Versioned local ownership records."""

    schema_version: Literal["1.0"] = "1.0"
    resources: tuple[OwnershipRecord, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_ownership_ids(self) -> Self:
        ownership_ids = [record.ownership_id for record in self.resources]
        if len(ownership_ids) != len(set(ownership_ids)):
            message = "ownership registry contains duplicate ownership IDs"
            raise ValueError(message)
        return self

    def find(self, ownership_id: UUID) -> OwnershipRecord | None:
        """Return the exact record, rejecting duplicate local identities."""

        return next(
            (record for record in self.resources if record.ownership_id == ownership_id),
            None,
        )


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
