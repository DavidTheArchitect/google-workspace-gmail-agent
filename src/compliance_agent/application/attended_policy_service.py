"""End-to-end attended preview, approval, browser execution, and verification."""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from compliance_agent.application.attended_audit import AttendedRunAudit
from compliance_agent.application.change_service import ChangeService
from compliance_agent.browser.admin_agent_driver import (
    AdminBrowserApplyResult,
    AdminBrowserPermit,
    PlaywrightAdminAgentSession,
)
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import (
    AddressListOwnershipRecord,
    ComplianceOwnershipRecord,
    OwnershipRecord,
    OwnershipRegistry,
)
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.identifiers import Uuid4Generator
from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.changes import (
    ChangeSet,  # noqa: TC001 - Pydantic resolves these field annotations at runtime.
    ComplianceChangeSet,  # noqa: TC001 - Pydantic resolves these at runtime.
)
from compliance_agent.schemas.compliance import ContentComplianceState
from compliance_agent.schemas.operations import RunMode
from compliance_agent.schemas.plan import (
    CreateContentComplianceRule,
    ListContentComplianceRules,
    RemoveContentComplianceRule,
    SetContentComplianceRuleEnabled,
    TaskPlan,
    UpdateContentComplianceRule,
)
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus

if TYPE_CHECKING:
    from compliance_agent.llm.group_chat import GroupChatTranscript
    from compliance_agent.settings import Settings

Surface = Literal["blocked_senders", "content_compliance"]
_LOGGER = logging.getLogger(__name__)


class AttendedPolicyPreview(FrozenModel):
    """Complete browser-backed evidence presented before an optional live approval."""

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mode: RunMode
    surface: Surface
    plan: TaskPlan
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest
    approval_phrase: str | None = None
    expires_at: datetime | None = None
    standard_before: BlockedSenderState | None = None
    standard_after: BlockedSenderState | None = None
    standard_change: ChangeSet | None = None
    compliance_before: ContentComplianceState | None = None
    compliance_after: ContentComplianceState | None = None
    compliance_change: ComplianceChangeSet | None = None

    @model_validator(mode="after")
    def require_surface_evidence(self) -> Self:
        standard = (
            self.standard_before,
            self.standard_after,
            self.standard_change,
        )
        compliance = (
            self.compliance_before,
            self.compliance_after,
            self.compliance_change,
        )
        if self.surface == "blocked_senders":
            if any(value is None for value in standard) or any(
                value is not None for value in compliance
            ):
                message = "blocked-sender preview requires only standard evidence"
                raise ValueError(message)
        elif any(value is None for value in compliance) or any(
            value is not None for value in standard
        ):
            message = "content-compliance preview requires only advanced evidence"
            raise ValueError(message)
        live_evidence = self.approval_phrase is not None and self.expires_at is not None
        if live_evidence != (self.mode == RunMode.LIVE):
            message = "only live previews carry approval evidence"
            raise ValueError(message)
        if self.expires_at is not None and (
            self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None
        ):
            message = "approval expiry must be timezone-aware"
            raise ValueError(message)
        return self

    @property
    def has_mutations(self) -> bool:
        change = self.standard_change or self.compliance_change
        return bool(change and change.has_mutations)


class AttendedExecutionResult(FrozenModel):
    """Terminal result after fresh drift check, write, and independent read-back."""

    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal[
        "completed",
        "no_change",
        "drifted",
        "verification_failed",
        "recovery_required",
    ]
    browser_result: AdminBrowserApplyResult | None = None
    verified: bool
    current_before_hash: Sha256Digest
    observed_after_hash: Sha256Digest | None = None
    replacement_preview: AttendedPolicyPreview | None = None


@dataclass(slots=True)
class _PendingRun:
    settings: Settings
    preview: AttendedPolicyPreview
    audit: AttendedRunAudit


class PendingAttendedRuns:
    """Server-only, process-local approval envelopes; client state never authorizes writes."""

    def __init__(self) -> None:
        self._runs: dict[str, _PendingRun] = {}

    def put(
        self,
        settings: Settings,
        preview: AttendedPolicyPreview,
        audit: AttendedRunAudit,
    ) -> None:
        self._runs[preview.run_id] = _PendingRun(
            settings=settings,
            preview=preview,
            audit=audit,
        )

    def get(self, run_id: str) -> _PendingRun | None:
        return self._runs.get(run_id)

    def discard(self, run_id: str) -> None:
        self._runs.pop(run_id, None)


class AttendedPolicyService:
    """Coordinate both Google blocking surfaces through the same exact approval lifecycle."""

    def __init__(self, pending: PendingAttendedRuns | None = None) -> None:
        self._pending = pending or PendingAttendedRuns()

    def cancel(self, run_id: str) -> None:
        """Invalidate one unused approval and close its audit trail."""

        pending = self._pending.get(run_id)
        if pending is None:
            return
        self._pending.discard(run_id)
        pending.audit.finalize(RunStatus.CONFIRMATION_REJECTED)

    def record_plan_review(
        self,
        settings: Settings,
        plan: TaskPlan,
        transcript: GroupChatTranscript,
    ) -> str:
        """Finalize a plan-only audit package containing the exact group review."""

        run_id = uuid4().hex
        started_at = datetime.now(UTC)
        audit = AttendedRunAudit(settings, run_id=run_id, started_at=started_at)
        audit.record_review(plan, transcript, model_tag=settings.ollama_model)
        audit.finalize(RunStatus.NO_CHANGE_REQUIRED)
        return run_id

    async def preview(
        self,
        settings: Settings,
        plan: TaskPlan,
        review: GroupChatTranscript | None = None,
    ) -> AttendedPolicyPreview:
        """Open a headed browser, read current state, and calculate an exact change."""

        if settings.run_mode == RunMode.PLAN_ONLY:
            message = "browser preview requires dry-run or live mode"
            raise ValueError(message)
        _require_browser_identities(settings)
        run_id = uuid4().hex
        started_at = datetime.now(UTC)
        surface = _plan_surface(plan)
        ownership = OwnershipStore(settings.state_dir).load()
        audit = AttendedRunAudit(
            settings,
            run_id=run_id,
            started_at=started_at,
        )
        if review is not None:
            audit.record_review(plan, review, model_tag=settings.ollama_model)
        try:
            async with PlaywrightAdminAgentSession(
                settings,
                run_id=run_id,
                started_at=started_at,
            ) as browser:
                if surface == "blocked_senders":
                    expected_standard = _standard_snapshot(
                        ownership,
                        _target_ou(plan, ownership),
                    )
                    current_standard = await browser.read_blocked_sender_state(
                        expected_standard,
                        managed_prefix=settings.managed_resource_prefix,
                    )
                    desired_standard, standard_change = _calculate_standard(
                        settings,
                        plan,
                        current_standard,
                        ownership,
                    )
                    preview = _standard_preview(
                        settings,
                        run_id,
                        started_at,
                        plan,
                        current_standard,
                        desired_standard,
                        standard_change,
                    )
                else:
                    expected_compliance = _compliance_snapshot(ownership)
                    current_compliance = await browser.read_content_compliance_state(
                        expected_compliance,
                        managed_prefix=settings.managed_resource_prefix,
                    )
                    desired_compliance, compliance_change = _calculate_compliance(
                        settings,
                        plan,
                        current_compliance,
                        ownership,
                    )
                    preview = _compliance_preview(
                        settings,
                        run_id,
                        started_at,
                        plan,
                        current_compliance,
                        desired_compliance,
                        compliance_change,
                    )
        except Exception:
            audit.finalize(RunStatus.FAILED_UNCHANGED)
            raise
        audit.record_preview(preview)
        if preview.mode == RunMode.LIVE and preview.has_mutations:
            self._pending.put(settings, preview, audit)
        else:
            audit.finalize(
                RunStatus.NO_CHANGE_REQUIRED
                if not preview.has_mutations
                else RunStatus.DRY_RUN_PREVIEW_READY
            )
        return preview

    async def execute(  # noqa: C901, PLR0912, PLR0915 - safety sequence is linear.
        self,
        run_id: str,
        *,
        phrase: str,
        acknowledged: bool,
    ) -> AttendedExecutionResult:
        """Consume one live approval after a fresh read, then write and verify."""

        pending = self._pending.get(run_id)
        if pending is None:
            message = "live approval is missing, consumed, or expired"
            raise ValueError(message)
        preview = pending.preview
        now = datetime.now(UTC)
        if preview.expires_at is None or now >= preview.expires_at:
            try:
                pending.audit.finalize(RunStatus.CONFIRMATION_REJECTED)
            finally:
                self._pending.discard(run_id)
            message = "live approval expired; create a fresh browser preview"
            raise ValueError(message)
        if (
            not acknowledged
            or preview.approval_phrase is None
            or not hmac.compare_digest(phrase.strip(), preview.approval_phrase)
        ):
            message = "approval acknowledgement or exact phrase is incorrect"
            raise ValueError(message)
        self._pending.discard(run_id)
        settings = pending.settings
        ownership_store = OwnershipStore(settings.state_dir)
        ownership = ownership_store.load()
        mutation_started = False
        try:
            async with PlaywrightAdminAgentSession(
                settings,
                run_id=run_id,
                started_at=now,
            ) as browser:
                fresh = await _fresh_read(browser, preview, settings, ownership)
                fresh_hash = canonical_hash(fresh)
                if fresh_hash != preview.before_state_hash:
                    result = AttendedExecutionResult(
                        run_id=run_id,
                        status="drifted",
                        verified=False,
                        current_before_hash=fresh_hash,
                    )
                    pending.audit.finalize(RunStatus.CONFIRMATION_REJECTED, result=result)
                    return result
                change = preview.standard_change or preview.compliance_change
                if change is None or not change.has_mutations:
                    result = AttendedExecutionResult(
                        run_id=run_id,
                        status="no_change",
                        verified=True,
                        current_before_hash=fresh_hash,
                        observed_after_hash=fresh_hash,
                    )
                    pending.audit.finalize(RunStatus.NO_CHANGE_REQUIRED, result=result)
                    return result
                permit = AdminBrowserPermit(
                    approval_id=uuid4().hex,
                    plan_hash=preview.plan_hash,
                    before_state_hash=preview.before_state_hash,
                    change_set_hash=preview.change_set_hash,
                    target_ou=_preview_target_ou(preview),
                    target_ownership_id=_preview_target_ownership_id(preview),
                    surface=preview.surface,
                    approved=True,
                )
                mutation_started = True
                if preview.standard_change is not None:
                    browser_result = await browser.apply_blocked_sender_change(
                        preview.standard_change,
                        permit,
                    )
                else:
                    browser_result = await browser.apply_content_compliance_change(
                        _required_compliance_change(preview),
                        permit,
                    )
                observed = await _verification_read(browser, preview, settings)
        except Exception:
            pending.audit.finalize(
                RunStatus.INDETERMINATE if mutation_started else RunStatus.FAILED_UNCHANGED
            )
            raise
        desired = preview.standard_after or preview.compliance_after
        verified = browser_result.completed and observed == desired
        observed_hash = canonical_hash(observed)
        if verified:
            try:
                _commit_ownership(ownership_store, ownership, preview, now)
            except Exception:
                _LOGGER.exception("Google state verified but ownership persistence failed")
                result = AttendedExecutionResult(
                    run_id=run_id,
                    status="recovery_required",
                    browser_result=browser_result,
                    verified=True,
                    current_before_hash=fresh_hash,
                    observed_after_hash=observed_hash,
                )
                pending.audit.finalize(RunStatus.INDETERMINATE, result=result)
                return result
        result = AttendedExecutionResult(
            run_id=run_id,
            status="completed" if verified else "verification_failed",
            browser_result=browser_result,
            verified=verified,
            current_before_hash=fresh_hash,
            observed_after_hash=observed_hash,
        )
        try:
            pending.audit.finalize(
                RunStatus.APPLIED_UI_VERIFIED if verified else RunStatus.INDETERMINATE,
                result=result,
            )
        except Exception:
            _LOGGER.exception("Terminal attended-run audit finalization failed")
            return result.model_copy(update={"status": "recovery_required"})
        return result


def _require_browser_identities(settings: Settings) -> None:
    if not settings.expected_admin_email or not settings.expected_workspace_domain:
        message = "configure the expected Google administrator and Workspace domain first"
        raise ValueError(message)


def _required_compliance_change(preview: AttendedPolicyPreview) -> ComplianceChangeSet:
    if preview.compliance_change is None:
        message = "live preview omitted its change set"
        raise ValueError(message)
    return preview.compliance_change


def _plan_surface(plan: TaskPlan) -> Surface:
    compliance_types = (
        CreateContentComplianceRule,
        UpdateContentComplianceRule,
        RemoveContentComplianceRule,
        SetContentComplianceRuleEnabled,
        ListContentComplianceRules,
    )
    advanced = [isinstance(action, compliance_types) for action in plan.actions]
    if any(advanced) and not all(advanced):
        message = "one attended run cannot mix standard and Content compliance surfaces"
        raise ValueError(message)
    return "content_compliance" if all(advanced) else "blocked_senders"


def _target_ou(plan: TaskPlan, ownership: OwnershipRegistry) -> str:
    targets: set[str] = set()
    for action in plan.actions:
        target = getattr(action, "target_ou", None)
        if target is not None:
            targets.add(target.path if hasattr(target, "path") else str(target))
            continue
        target_rule_id = getattr(action, "target_rule_id", None)
        if target_rule_id is not None:
            record = ownership.find(target_rule_id)
            if record is None:
                message = "target blocked-sender ownership record was not found"
                raise ValueError(message)
            targets.add(record.target_ou)
    if not targets:
        targets.add("/")
    if len(targets) != 1:
        message = "one attended run must target exactly one organizational unit"
        raise ValueError(message)
    return next(iter(targets))


def _standard_snapshot(registry: OwnershipRegistry, target_ou: str) -> BlockedSenderState:
    records = tuple(record for record in registry.resources if record.target_ou == target_ou)
    missing = [record.rule_display_name for record in records if record.rule_snapshot is None]
    if missing:
        message = "managed blocked-sender ownership predates browser snapshots; recover it first"
        raise ValueError(message)
    rules = tuple(record.rule_snapshot for record in records if record.rule_snapshot is not None)
    referenced_names = {
        name for rule in rules for name in rule.address_list_names + rule.bypass_address_list_names
    }
    lists = [
        record.address_list_snapshot
        for record in records
        if record.address_list_snapshot is not None
        and record.address_list_snapshot.display_name in referenced_names
    ]
    lists.extend(
        record.address_list_snapshot
        for record in registry.address_lists
        if record.target_ou == target_ou
        and record.address_list_snapshot is not None
        and record.address_list_snapshot.display_name in referenced_names
    )
    if {item.display_name for item in lists} != referenced_names:
        message = "managed blocked-sender address-list snapshots are incomplete; recover them first"
        raise ValueError(message)
    return BlockedSenderState(
        target_ou=target_ou,
        rules=rules,
        address_lists=tuple(lists),
    )


def _compliance_snapshot(registry: OwnershipRegistry) -> ContentComplianceState:
    missing = [
        record.display_name for record in registry.compliance_rules if record.rule_snapshot is None
    ]
    if missing:
        message = (
            "managed Content compliance ownership predates browser snapshots; recover it first"
        )
        raise ValueError(message)
    return ContentComplianceState(
        rules=tuple(
            record.rule_snapshot
            for record in registry.compliance_rules
            if record.rule_snapshot is not None
        )
    )


def _calculate_standard(
    settings: Settings,
    plan: TaskPlan,
    current: BlockedSenderState,
    ownership: OwnershipRegistry,
) -> tuple[BlockedSenderState, ChangeSet]:
    desired, change = ChangeService(
        Uuid4Generator(),
        settings.managed_resource_prefix,
    ).calculate(plan, current, ownership)
    return desired.desired_state, change


def _calculate_compliance(
    settings: Settings,
    plan: TaskPlan,
    current: ContentComplianceState,
    ownership: OwnershipRegistry,
) -> tuple[ContentComplianceState, ComplianceChangeSet]:
    return ChangeService(
        Uuid4Generator(),
        settings.managed_resource_prefix,
    ).calculate_compliance(plan, current, ownership)


def _approval_evidence(
    settings: Settings,
    run_id: str,
    started_at: datetime,
) -> tuple[str | None, datetime | None]:
    if settings.run_mode != RunMode.LIVE:
        return None, None
    return f"APPLY {run_id[:4].upper()}", started_at + timedelta(
        seconds=settings.approval_ttl_seconds
    )


def _standard_preview(  # noqa: PLR0913 - explicit evidence is assembled once.
    settings: Settings,
    run_id: str,
    started_at: datetime,
    plan: TaskPlan,
    current: BlockedSenderState,
    desired: BlockedSenderState,
    change: ChangeSet,
) -> AttendedPolicyPreview:
    phrase, expiry = _approval_evidence(settings, run_id, started_at)
    return AttendedPolicyPreview(
        run_id=run_id,
        mode=settings.run_mode,
        surface="blocked_senders",
        plan=plan,
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(current),
        change_set_hash=canonical_hash(change),
        approval_phrase=phrase,
        expires_at=expiry,
        standard_before=current,
        standard_after=desired,
        standard_change=change,
    )


def _compliance_preview(  # noqa: PLR0913 - explicit evidence is assembled once.
    settings: Settings,
    run_id: str,
    started_at: datetime,
    plan: TaskPlan,
    current: ContentComplianceState,
    desired: ContentComplianceState,
    change: ComplianceChangeSet,
) -> AttendedPolicyPreview:
    phrase, expiry = _approval_evidence(settings, run_id, started_at)
    return AttendedPolicyPreview(
        run_id=run_id,
        mode=settings.run_mode,
        surface="content_compliance",
        plan=plan,
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(current),
        change_set_hash=canonical_hash(change),
        approval_phrase=phrase,
        expires_at=expiry,
        compliance_before=current,
        compliance_after=desired,
        compliance_change=change,
    )


async def _fresh_read(
    browser: PlaywrightAdminAgentSession,
    preview: AttendedPolicyPreview,
    settings: Settings,
    ownership: OwnershipRegistry,
) -> BlockedSenderState | ContentComplianceState:
    if preview.surface == "blocked_senders":
        return await browser.read_blocked_sender_state(
            _standard_snapshot(ownership, _preview_target_ou(preview)),
            managed_prefix=settings.managed_resource_prefix,
        )
    return await browser.read_content_compliance_state(
        _compliance_snapshot(ownership),
        managed_prefix=settings.managed_resource_prefix,
    )


async def _verification_read(
    browser: PlaywrightAdminAgentSession,
    preview: AttendedPolicyPreview,
    settings: Settings,
) -> BlockedSenderState | ContentComplianceState:
    if preview.standard_after is not None:
        return await browser.read_blocked_sender_state(
            preview.standard_after,
            managed_prefix=settings.managed_resource_prefix,
        )
    if preview.compliance_after is None:
        message = "preview omitted desired verification state"
        raise ValueError(message)
    return await browser.read_content_compliance_state(
        preview.compliance_after,
        managed_prefix=settings.managed_resource_prefix,
    )


def _preview_target_ou(preview: AttendedPolicyPreview) -> str:
    if preview.standard_after is not None:
        return preview.standard_after.target_ou
    if preview.compliance_change is None:
        message = "preview omitted advanced change evidence"
        raise ValueError(message)
    touched = (
        preview.compliance_change.rules_to_create
        + preview.compliance_change.rules_to_update
        + preview.compliance_change.rules_to_remove
    )
    if len(touched) != 1:
        message = "one approved advanced run must touch exactly one rule"
        raise ValueError(message)
    return touched[0].target_ou.path


def _preview_target_ownership_id(preview: AttendedPolicyPreview) -> UUID:
    """Resolve the approved policy even when only its linked address list changes."""

    explicit_targets: set[UUID] = set()
    for action in preview.plan.actions:
        target = getattr(action, "target_rule_id", None)
        if isinstance(target, UUID):
            explicit_targets.add(target)
        elif target is not None:
            message = "plan contains an invalid managed policy identity"
            raise TypeError(message)
    if explicit_targets:
        if len(explicit_targets) != 1:
            message = "one approval must target exactly one managed policy"
            raise ValueError(message)
        return next(iter(explicit_targets))
    change = preview.standard_change or preview.compliance_change
    if change is None:
        message = "preview omitted its change evidence"
        raise ValueError(message)
    created_rules = change.rules_to_create
    if len(created_rules) != 1:
        message = "one approval must create exactly one managed policy"
        raise ValueError(message)
    return created_rules[0].ownership_id


def _commit_ownership(
    store: OwnershipStore,
    current: OwnershipRegistry,
    preview: AttendedPolicyPreview,
    now: datetime,
) -> None:
    resources = {record.ownership_id: record for record in current.resources}
    address_lists = {record.ownership_id: record for record in current.address_lists}
    compliance = {record.ownership_id: record for record in current.compliance_rules}
    if preview.standard_after is not None and preview.standard_change is not None:
        removed_rule_ids = {rule.ownership_id for rule in preview.standard_change.rules_to_remove}
        removed_list_ids = {
            address_list.ownership_id
            for address_list in preview.standard_change.address_lists_to_remove
        }
        for ownership_id in removed_rule_ids:
            resources.pop(ownership_id, None)
        for ownership_id in removed_list_ids:
            address_lists.pop(ownership_id, None)
        lists_by_id = {
            address_list.ownership_id: address_list
            for address_list in preview.standard_after.address_lists
        }
        for rule in preview.standard_after.rules:
            paired = lists_by_id.get(rule.ownership_id)
            existing_resource = resources.get(rule.ownership_id)
            resources[rule.ownership_id] = OwnershipRecord(
                ownership_id=rule.ownership_id,
                rule_display_name=rule.display_name,
                address_list_display_name=(
                    paired.display_name if paired else rule.address_list_names[0]
                ),
                target_ou=rule.target_ou,
                created_at=existing_resource.created_at if existing_resource else now,
                rule_snapshot=rule,
                address_list_snapshot=paired,
            )
        paired_ids = set(resources)
        for ownership_id, address_list in lists_by_id.items():
            if ownership_id in paired_ids:
                continue
            existing_address = address_lists.get(ownership_id)
            address_lists[ownership_id] = AddressListOwnershipRecord(
                ownership_id=ownership_id,
                display_name=address_list.display_name,
                target_ou=preview.standard_after.target_ou,
                purpose="bypass",
                created_at=existing_address.created_at if existing_address else now,
                address_list_snapshot=address_list,
            )
    if preview.compliance_after is not None and preview.compliance_change is not None:
        for removed_rule in preview.compliance_change.rules_to_remove:
            compliance.pop(removed_rule.ownership_id, None)
        for compliance_rule in preview.compliance_after.rules:
            existing_compliance = compliance.get(compliance_rule.ownership_id)
            compliance[compliance_rule.ownership_id] = ComplianceOwnershipRecord(
                ownership_id=compliance_rule.ownership_id,
                display_name=compliance_rule.display_name,
                target_ou=compliance_rule.target_ou.path,
                created_at=existing_compliance.created_at if existing_compliance else now,
                rule_snapshot=compliance_rule,
            )
    store.save(
        OwnershipRegistry(
            resources=tuple(sorted(resources.values(), key=lambda item: item.ownership_id.hex)),
            address_lists=tuple(
                sorted(address_lists.values(), key=lambda item: item.ownership_id.hex)
            ),
            compliance_rules=tuple(
                sorted(compliance.values(), key=lambda item: item.ownership_id.hex)
            ),
        )
    )


ATTENDED_POLICY_SERVICE = AttendedPolicyService()
