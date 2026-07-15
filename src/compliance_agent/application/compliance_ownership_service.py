"""Ownership lifecycle for verified content-compliance rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from compliance_agent.application.ownership_service import (
    OwnershipRegistryStore,
    OwnershipUpdate,
)
from compliance_agent.domain.ownership import (
    ComplianceOwnershipRecord,
    OwnershipRegistry,
)
from compliance_agent.exceptions import OwnershipNotEstablished

if TYPE_CHECKING:
    from uuid import UUID

    from compliance_agent.infrastructure.clock import Clock
    from compliance_agent.schemas.changes import ComplianceChangeSet
    from compliance_agent.schemas.results import ComplianceVerificationResult


class ComplianceOwnershipLifecycleService:
    """Commit advanced-rule evidence only after exact post-write verification."""

    def __init__(self, store: OwnershipRegistryStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def commit_verified(
        self,
        change_set: ComplianceChangeSet,
        verification: ComplianceVerificationResult,
    ) -> OwnershipUpdate:
        """Atomically update compliance ownership records after a matched read-back."""

        if verification.status != "matched":
            return OwnershipUpdate()
        if (
            verification.desired_state != change_set.expected_after
            or verification.observed_state != change_set.expected_after
        ):
            message = "compliance ownership verification does not match expected state"
            raise OwnershipNotEstablished(message)
        registry = self._store.load()
        records = {record.ownership_id: record for record in registry.compliance_rules}
        added: list[UUID] = []
        for rule in change_set.rules_to_create:
            proposed = ComplianceOwnershipRecord(
                ownership_id=rule.ownership_id,
                display_name=rule.display_name,
                target_ou=rule.target_ou.path,
                created_at=self._clock.now(),
            )
            existing = records.get(rule.ownership_id)
            if existing is not None and (
                existing.display_name != proposed.display_name
                or existing.target_ou != proposed.target_ou
            ):
                message = f"compliance ownership evidence conflicts for {rule.ownership_id}"
                raise OwnershipNotEstablished(message)
            if existing is None:
                records[rule.ownership_id] = proposed
                added.append(rule.ownership_id)
        removed = [
            rule.ownership_id for rule in change_set.rules_to_remove if rule.ownership_id in records
        ]
        for ownership_id in removed:
            del records[ownership_id]
        update = OwnershipUpdate(
            added=tuple(sorted(added, key=str)),
            removed=tuple(sorted(removed, key=str)),
        )
        if update.added or update.removed:
            self._store.save(
                OwnershipRegistry(
                    resources=registry.resources,
                    address_lists=registry.address_lists,
                    compliance_rules=tuple(
                        sorted(records.values(), key=lambda item: item.ownership_id.hex)
                    ),
                )
            )
        return update
