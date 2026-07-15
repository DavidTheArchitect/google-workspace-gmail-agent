"""Complete normalized blocked-sender state."""

from typing import Literal, Self

from pydantic import model_validator

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule


class BlockedSenderState(FrozenModel):
    """Observed or desired root-OU state, including read-only unmanaged rule names."""

    schema_version: Literal["1.0"] = "1.0"
    target_ou: str = "/"
    rules: tuple[ManagedBlockedSenderRule, ...] = ()
    address_lists: tuple[ManagedAddressList, ...] = ()
    unmanaged_rule_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_owned_resources(self) -> Self:
        rule_ids = [rule.ownership_id for rule in self.rules]
        list_ids = [address_list.ownership_id for address_list in self.address_lists]
        if len(rule_ids) != len(set(rule_ids)):
            message = "state contains duplicate managed rule ownership IDs"
            raise ValueError(message)
        if len(list_ids) != len(set(list_ids)):
            message = "state contains duplicate managed address-list ownership IDs"
            raise ValueError(message)
        return self
