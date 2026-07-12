"""Explicit recovery of local ownership evidence from intact prior audits."""

from pathlib import Path
from uuid import UUID

from compliance_agent.application.ownership_service import OwnershipRegistryStore
from compliance_agent.audit.manifest import RunManifest, verify_manifest
from compliance_agent.audit.writer import verify_event_chain
from compliance_agent.domain.ownership import OwnershipRecord, OwnershipRegistry
from compliance_agent.exceptions import OwnershipNotEstablished
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.resources import ManagedAddressList, ManagedBlockedSenderRule
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus

_VERIFIED_CREATION_STATUSES = frozenset(
    {RunStatus.APPLIED_UI_VERIFIED, RunStatus.APPLIED_PENDING_PROPAGATION}
)


class OwnershipRecoveryService:
    """Rebuild one record only from verified creation and matching observed evidence."""

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
        audited_rule, audited_list = _verified_creation_pair(
            audit_run,
            manifest,
            audited_state,
            ownership_id,
        )
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


def has_exact_pair(state: BlockedSenderState, ownership_id: UUID) -> bool:
    """Report whether the state holds exactly one intact owned rule/list pair."""

    try:
        _exact_pair(state, ownership_id)
    except OwnershipNotEstablished:
        return False
    return True


def has_verified_creation(
    audit_run: Path,
    observed_state: BlockedSenderState,
    ownership_id: UUID,
) -> bool:
    """Return whether an intact applied audit proves creation of the observed pair."""

    try:
        manifest = _validated_manifest(audit_run)
        audited_state = _audited_after_state(audit_run)
        audited_pair = _verified_creation_pair(
            audit_run,
            manifest,
            audited_state,
            ownership_id,
        )
        observed_pair = _exact_pair(observed_state, ownership_id)
    except OwnershipNotEstablished:
        return False
    return audited_pair == observed_pair


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


def _verified_creation_pair(
    run_directory: Path,
    manifest: RunManifest,
    audited_state: BlockedSenderState,
    ownership_id: UUID,
) -> tuple[ManagedBlockedSenderRule, ManagedAddressList]:
    if manifest.final_status not in _VERIFIED_CREATION_STATUSES:
        message = "ownership recovery requires a verified applied creation run"
        raise OwnershipNotEstablished(message)
    try:
        report = RunResult.model_validate_json(
            (run_directory / "report.json").read_text(encoding="utf-8")
        )
        change_set = ChangeSet.model_validate_json(
            (run_directory / "change_set.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as error:
        message = "ownership recovery requires a valid applied report and change set"
        raise OwnershipNotEstablished(message) from error
    if report.status != manifest.final_status or change_set.expected_after != audited_state:
        message = "ownership recovery audit outcome does not match its verified after-state"
        raise OwnershipNotEstablished(message)
    created_rules = [
        rule for rule in change_set.rules_to_create if rule.ownership_id == ownership_id
    ]
    created_lists = [
        item for item in change_set.address_lists_to_create if item.ownership_id == ownership_id
    ]
    if len(created_rules) != 1 or len(created_lists) != 1:
        message = "ownership recovery audit does not prove creation of the exact pair"
        raise OwnershipNotEstablished(message)
    audited_pair = _exact_pair(audited_state, ownership_id)
    if audited_pair != (created_rules[0], created_lists[0]):
        message = "ownership recovery created pair does not match verified after-state"
        raise OwnershipNotEstablished(message)
    return audited_pair


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
