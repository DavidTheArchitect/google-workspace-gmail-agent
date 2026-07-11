"""Address-list and blocked-sender resource models."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.domain.normalization import normalize_address
from compliance_agent.schemas.base import FrozenModel


class AddressEntry(FrozenModel):
    """One validated display value and its application-controlled comparison value."""

    kind: Literal["email", "domain"]
    value: str = Field(min_length=1, max_length=254)
    normalized_value: str = ""

    @model_validator(mode="after")
    def calculate_normalized_value(self) -> Self:
        normalized = normalize_address(self.kind, self.value)
        if self.normalized_value and self.normalized_value != normalized:
            message = "normalized_value does not match deterministic normalization"
            raise ValueError(message)
        object.__setattr__(self, "normalized_value", normalized)
        object.__setattr__(self, "value", self.value.strip())
        return self


class ManagedAddressList(FrozenModel):
    """An address list associated with an immutable application ownership ID."""

    ownership_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    entries: tuple[AddressEntry, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_entries(self) -> Self:
        normalized_entries = [entry.normalized_value for entry in self.entries]
        if len(normalized_entries) != len(set(normalized_entries)):
            message = "managed address list contains duplicate normalized entries"
            raise ValueError(message)
        return self


class ManagedBlockedSenderRule(FrozenModel):
    """A root-OU rule whose visible identity can be reconciled with local ownership."""

    ownership_id: UUID
    display_name: str = Field(min_length=1, max_length=200)
    target_ou: Literal["/"] = "/"
    address_list_names: tuple[str, ...]
    rejection_notice: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_notice_and_lists(self) -> Self:
        if not self.address_list_names:
            message = "blocked-sender rule must reference at least one address list"
            raise ValueError(message)
        if len(self.address_list_names) != len(set(self.address_list_names)):
            message = "blocked-sender rule contains duplicate address-list names"
            raise ValueError(message)
        if self.rejection_notice is not None:
            notice = self.rejection_notice.strip()
            if not notice:
                message = "rejection notice cannot be blank"
                raise ValueError(message)
            object.__setattr__(self, "rejection_notice", notice)
        return self
