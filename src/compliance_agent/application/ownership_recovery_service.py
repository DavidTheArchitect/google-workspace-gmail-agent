"""Explicit recovery of local ownership evidence from intact prior audits."""

from pathlib import Path
from uuid import UUID

from compliance_agent.application.ownership_service import OwnershipRegistryStore
from compliance_agent.audit.manifest import RunManifest, verify_manifest
from compliance_agent.audit.writer import verify_event_chain
from compliance_agent.domain.ownership import OwnershipRecord, OwnershipRegistry
from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.state import BlockedSenderState


class OwnershipRecoveryService:
    """Rebuild one missing registry record only from exact dual historical/current evidence."""

    def __init__(self, store: OwnershipRegistryStore) -> None:
        self._store = store

    def recover(
        self,
        ownership_id: UUID,
        current_state: BlockedSenderState,
        audit_run: Path,
        confirmation: str,
    ) -> OwnershipRecord:
        """Persist one recovered record after integrity and relationship verification."""

        expected = f"RECOVER {ownership_id.hex[:8].upper()}"
        if confirmation.strip() != expected:
            message = f"ownership recovery requires exact confirmation: {expected}"
            raise OwnershipNotEstablished(message)
        manifest = _validated_manifest(audit_run)
        audited_state = _audited_after_state(audit_run)
        audited_rule, audited_list = _exact_pair(audited_state, ownership_id)
        current_rule, current_list = _exact_pair(current_state, ownership_id)
        if audited_rule != current_rule or audited_list != current_list:
            message = "current UI ownership pair does not match intact historical evidence"
            raise OwnershipNotEstablished(message)
        registry = self._store.load()
        if registry.find(ownership_id) is not None:
            message = f"ownership registry already contains {ownership_id}"
            raise OwnershipNotEstablished(message)
        record = OwnershipRecord(
            ownership_id=ownership_id,
            rule_display_name=current_rule.display_name,
            address_list_display_name=current_list.display_name,
            created_at=manifest.end_time,
        )
        self._store.save(
            OwnershipRegistry(
                resources=tuple(
                    sorted((*registry.resources, record), key=lambda item: item.ownership_id.hex)
                )
            )
        )
        return record


def _validated_manifest(run_directory: Path) -> RunManifest:
    manifest_path = run_directory / "manifest.json"
    try:
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        message = "ownership recovery audit manifest is unavailable or invalid"
        raise OwnershipNotEstablished(message) from error
    errors = (
        *verify_manifest(run_directory, manifest),
        *verify_event_chain(run_directory / "run.jsonl"),
    )
    if errors:
        message = "ownership recovery audit integrity verification failed"
        raise OwnershipNotEstablished(message)
    return manifest


def _audited_after_state(run_directory: Path) -> BlockedSenderState:
    try:
        return BlockedSenderState.model_validate_json(
            (run_directory / "after.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        message = "ownership recovery requires a valid audited after-state"
        raise OwnershipNotEstablished(message) from error


def _exact_pair(
    state: BlockedSenderState,
    ownership_id: UUID,
) -> tuple[ManagedBlockedSenderRule, ManagedAddressList]:
    rules = [rule for rule in state.rules if rule.ownership_id == ownership_id]
    address_lists = [item for item in state.address_lists if item.ownership_id == ownership_id]
    if len(rules) != 1 or len(address_lists) != 1:
        message = "ownership recovery requires one exact rule/address-list pair"
        raise OwnershipNotEstablished(message)
    rule = rules[0]
    address_list = address_lists[0]
    if rule.address_list_names != (address_list.display_name,):
        message = "ownership recovery relationship is not exact"
        raise OwnershipNotEstablished(message)
    return rule, address_list
