"""Deterministic desired-state difference models."""

from typing import Literal

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.state import BlockedSenderState


class StateDifference(FrozenModel):
    """One machine-readable mismatch between desired and observed state."""

    path: str
    kind: Literal["missing", "unexpected", "changed", "duplicate", "indeterminate"]
    expected: str | None = None
    observed: str | None = None


class ChangeSet(FrozenModel):
    """Exact resources changed between one current and desired state."""

    schema_version: Literal["1.0"] = "1.0"
    before_state: BlockedSenderState
    expected_after: BlockedSenderState
    rules_to_create: tuple[ManagedBlockedSenderRule, ...] = ()
    rules_to_update: tuple[ManagedBlockedSenderRule, ...] = ()
    rules_to_remove: tuple[ManagedBlockedSenderRule, ...] = ()
    address_lists_to_create: tuple[ManagedAddressList, ...] = ()
    address_lists_to_update: tuple[ManagedAddressList, ...] = ()
    address_lists_to_remove: tuple[ManagedAddressList, ...] = ()

    @property
    def has_mutations(self) -> bool:
        """Return whether the change set contains any external write."""

        return any(
            (
                self.rules_to_create,
                self.rules_to_update,
                self.rules_to_remove,
                self.address_lists_to_create,
                self.address_lists_to_update,
                self.address_lists_to_remove,
            )
        )


class DesiredStateResult(FrozenModel):
    """Pure desired-state calculation output with rule-wide impact metadata."""

    desired_state: BlockedSenderState
    notice_affected_entry_count: int = 0
