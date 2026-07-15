"""Dual-evidence ownership policy for application-managed resources."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import ManagedContentComplianceRule
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule


class OwnershipRecord(FrozenModel):
    """Local evidence paired with visible resource markers in the Admin console."""

    ownership_id: UUID
    rule_display_name: str = Field(min_length=1)
    address_list_display_name: str = Field(min_length=1)
    target_ou: str = Field(default="/", min_length=1, max_length=1_000)
    created_at: datetime

    @model_validator(mode="after")
    def require_aware_creation_time(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            message = "ownership creation time must be timezone-aware"
            raise ValueError(message)
        return self


class AddressListOwnershipRecord(FrozenModel):
    """Local evidence for a managed address list not paired by identity with a rule."""

    ownership_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    target_ou: str = Field(default="/", min_length=1, max_length=1_000)
    purpose: Literal["bypass", "condition"]
    created_at: datetime

    @model_validator(mode="after")
    def require_aware_creation_time(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            message = "ownership creation time must be timezone-aware"
            raise ValueError(message)
        return self


class ComplianceOwnershipRecord(FrozenModel):
    """Local evidence paired with a visible Gmail content-compliance rule marker."""

    ownership_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    target_ou: str = Field(min_length=1, max_length=1_000)
    created_at: datetime

    @model_validator(mode="after")
    def require_aware_creation_time(self) -> Self:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            message = "ownership creation time must be timezone-aware"
            raise ValueError(message)
        return self


class OwnershipRegistry(FrozenModel):
    """Versioned local ownership records."""

    schema_version: Literal["1.0", "2.0"] = "2.0"
    resources: tuple[OwnershipRecord, ...] = ()
    address_lists: tuple[AddressListOwnershipRecord, ...] = ()
    compliance_rules: tuple[ComplianceOwnershipRecord, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_ownership_ids(self) -> Self:
        ownership_ids = [record.ownership_id for record in self.resources]
        ownership_ids.extend(record.ownership_id for record in self.address_lists)
        ownership_ids.extend(record.ownership_id for record in self.compliance_rules)
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

    def find_address_list(
        self, ownership_id: UUID
    ) -> OwnershipRecord | AddressListOwnershipRecord | None:
        """Return paired or independent address-list ownership evidence."""

        paired = self.find(ownership_id)
        if paired is not None:
            return paired
        return next(
            (record for record in self.address_lists if record.ownership_id == ownership_id),
            None,
        )

    def find_compliance(self, ownership_id: UUID) -> ComplianceOwnershipRecord | None:
        """Return exact content-compliance ownership evidence."""

        return next(
            (record for record in self.compliance_rules if record.ownership_id == ownership_id),
            None,
        )


def managed_resource_names(prefix: str, ownership_id: UUID) -> tuple[str, str]:
    """Create predictable visible names without weakening UUID ownership identity."""

    short_id = ownership_id.hex[:8]
    return (f"{prefix} Block rule {short_id}", f"{prefix} Addresses {short_id}")


def managed_compliance_rule_name(prefix: str, ownership_id: UUID) -> str:
    """Create a predictable visible content-compliance rule marker."""

    return f"{prefix} Compliance rule {ownership_id.hex[:8]}"


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
) -> OwnershipRecord | AddressListOwnershipRecord:
    """Require exact visible marker and matching local address-list evidence."""

    record = registry.find_address_list(address_list.ownership_id)
    is_visible = address_list.display_name.startswith(f"{prefix} ")
    recorded_name = (
        record.address_list_display_name
        if isinstance(record, OwnershipRecord)
        else record.display_name
        if record is not None
        else None
    )
    if record is None or not is_visible or recorded_name != address_list.display_name:
        message = f"ownership is not established for address list {address_list.ownership_id}"
        raise OwnershipNotEstablished(message)
    return record


def require_owned_compliance_rule(
    rule: ManagedContentComplianceRule,
    registry: OwnershipRegistry,
    prefix: str,
) -> ComplianceOwnershipRecord:
    """Require local and visible ownership evidence for an advanced compliance rule."""

    record = registry.find_compliance(rule.ownership_id)
    if (
        record is None
        or not rule.display_name.startswith(f"{prefix} ")
        or record.display_name != rule.display_name
        or record.target_ou != rule.target_ou.path
    ):
        message = f"ownership is not established for compliance rule {rule.ownership_id}"
        raise OwnershipNotEstablished(message)
    return record
