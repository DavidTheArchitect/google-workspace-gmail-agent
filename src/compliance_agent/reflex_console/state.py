"""Server-owned Reflex state for policy composition and exact approval."""

from __future__ import annotations

import asyncio
import logging
import shutil
import webbrowser
from collections.abc import AsyncIterator  # noqa: TC003 - Reflex resolves event hints at runtime.
from datetime import datetime
from pathlib import Path
from uuid import UUID

import reflex as rx

from compliance_agent.application.attended_policy_service import (
    ATTENDED_POLICY_SERVICE,
    AttendedPolicyPreview,
)
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.console.configuration import LocalConfigurationStore
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.regex_validation import validate_google_regex
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.llm.group_chat import PARTICIPANT_SPECS, GroupChatTranscript
from compliance_agent.llm.persona import DEFAULT_PERSONA_ATTEMPTS, profile_signature
from compliance_agent.llm.planner import build_group_chat_reviewer, build_persona_generator
from compliance_agent.llm.readiness import (
    list_local_models,
    pull_local_model,
    require_local_model,
)
from compliance_agent.schemas.compliance import (
    AddressListCondition,
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    ComplianceExpression,
    ContentComplianceRuleDraft,
    EnvelopeFilter,
    ExpressionCombiner,
    GeneratedRejectionNotice,
    ManagedContentComplianceRule,
    MessageDirection,
    MetadataAttribute,
    MetadataMatch,
    OrganizationalUnitRef,
    PersonaProfile,
    PredefinedContentMatch,
    SimpleContentMatch,
)
from compliance_agent.schemas.operations import RunMode
from compliance_agent.schemas.plan import (
    CreateContentComplianceRule,
    ListBlockedSenderRules,
    ListContentComplianceRules,
    RemoveBlockedSenderRule,
    RemoveContentComplianceRule,
    SetBlockedSenderRuleEnabled,
    SetContentComplianceRuleEnabled,
    TaskPlan,
    UpdateBlockedSenderRule,
    UpdateContentComplianceRule,
)
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.settings import Settings, load_settings

_SHA256_HEX_LENGTH = 64
_MAX_ADDITIONAL_EXPRESSIONS = 9
_ADVANCED_LOCATIONS = frozenset(item.value for item in AdvancedContentLocation)
_ADVANCED_MATCH_TYPES = frozenset(item.value for item in AdvancedMatchType)
_EXPRESSION_TYPE_LABELS = {
    "advanced": "Advanced",
    "simple": "Simple",
    "metadata": "Metadata",
    "predefined": "Predefined",
}
_LOCATION_LABELS = {
    "full_headers": "Full headers",
    "headers_and_body": "Headers and body",
    "body": "Body",
    "subject": "Subject",
    "sender_header": "Sender header",
    "recipient_header": "Recipient header",
    "envelope_sender": "Envelope sender",
    "envelope_recipient": "Envelope recipient",
    "raw_message": "Raw message",
}
_MATCH_TYPE_LABELS = {
    "matches_regex": "Matches regex",
    "not_matches_regex": "Does not match regex",
    "contains": "Contains",
    "not_contains": "Does not contain",
    "equals": "Equals",
    "starts_with": "Starts with",
    "ends_with": "Ends with",
    "is_empty": "Is empty",
    "matches_any_word": "Matches any word",
    "matches_all_words": "Matches all words",
}
_ADDRESS_LIST_MODE_LABELS = {
    "none": "No address-list condition",
    "bypass": "Bypass listed addresses",
    "only_apply": "Only apply to listed addresses",
}
_FILTER_SELECTOR_LABELS = {
    "single_address": "Single address",
    "pattern": "Pattern (RE2)",
    "group_membership": "Group membership",
}
_METADATA_ATTRIBUTE_LABELS = {
    "message_authentication": "Message authentication",
    "source_ip": "Source IP",
    "secure_transport": "Secure transport (TLS)",
    "smime_encryption": "S/MIME encryption",
    "smime_signature": "S/MIME signature",
    "message_size": "Message size",
    "gmail_confidential_mode": "Gmail confidential mode",
    "security_sandbox_malware": "Security sandbox malware",
}
_METADATA_OPERATORS = {
    "message_authentication": ("authenticated", "not_authenticated"),
    "source_ip": ("within_range", "not_within_range"),
    "secure_transport": ("tls", "not_tls"),
    "smime_encryption": ("encrypted", "not_encrypted"),
    "smime_signature": ("signed", "not_signed"),
    "message_size": ("greater_than_mb", "less_than_mb"),
    "gmail_confidential_mode": ("confidential", "not_confidential"),
    "security_sandbox_malware": ("malware_detected",),
}
_METADATA_OPERATOR_LABELS = {
    "authenticated": "Authenticated",
    "not_authenticated": "Not authenticated",
    "within_range": "Within range",
    "not_within_range": "Not within range",
    "tls": "Uses TLS",
    "not_tls": "Does not use TLS",
    "encrypted": "Encrypted",
    "not_encrypted": "Not encrypted",
    "signed": "Signed",
    "not_signed": "Not signed",
    "greater_than_mb": "Greater than (MB)",
    "less_than_mb": "Less than (MB)",
    "confidential": "Confidential mode",
    "not_confidential": "Not confidential mode",
    "malware_detected": "Malware detected",
}
_LOGGER = logging.getLogger(__name__)
_MAX_NOTICE_CHARACTERS = 1_000
_PERSONA_TIMEOUT_MARGIN_SECONDS = 5.0
_PERSONA_HISTORY_LIMIT = 6
_STARTER_PERSONA_ROLE = "No generated persona"
_STARTER_PERSONA_VOICE = "Neutral starter draft"
_STARTER_PERSONA_MOTIF = "None"
_STARTER_PERSONA_TRAITS = ("neutral",)


def _idle_agent_activity() -> list[dict[str, str]]:
    return [
        {
            "name": spec.display_name,
            "time": "Pending",
            "icon": spec.icon,
            "status": "Waiting for a typed policy draft to review.",
            "findings": "",
        }
        for spec in PARTICIPANT_SPECS
    ]


def _starter_notice() -> str:
    return (
        "Delivery was refused because this message did not meet the recipient organization's "
        "email policy. Contact the recipient through another published channel if you need help."
    )


async def _review_plan(plan: TaskPlan) -> GroupChatTranscript:
    """Run a real Microsoft Agent Framework group chat over the typed proposal."""

    settings = load_settings()
    await require_local_model(settings, settings.ollama_model, require_vision=False)
    request = (
        "The following proposal has already passed deterministic schema validation. Review its "
        "Google Gmail semantics, RE2 behavior, safety gates, and operator clarity. Do not change "
        "it and do not claim execution.\n"
        f"{plan.model_dump_json()}"
    )
    async with asyncio.timeout(settings.group_chat_timeout_seconds):
        return await build_group_chat_reviewer(settings).review(request)


def _review_failure_message(error: Exception) -> str:
    """Return an operator-safe explanation without exposing provider internals."""

    settings = load_settings()
    if isinstance(error, TimeoutError):
        return (
            "The typed draft is ready, but the local specialist group reached its bounded "
            "time limit. Verify Ollama capacity and retry; approval remains locked."
        )
    if "not found" in str(error).lower():
        return (
            f"The typed draft is ready, but local model {settings.ollama_model!r} is not "
            "available in Ollama. Install that model or update CA_OLLAMA_MODEL, then retry."
        )
    return (
        "The typed draft is ready, but the local specialist group could not finish. "
        "Verify that Ollama is running and retry; approval remains locked."
    )


def _persona_generation_budget_seconds(settings: Settings) -> float:
    """Budget every bounded persona attempt, not just one model request.

    Duplicate suppression and the sender-safety quality gate legitimately
    consume several attempts, so the overall limit must cover each attempt's
    own per-request client timeout.
    """

    return (
        settings.llm_request_timeout_seconds * DEFAULT_PERSONA_ATTEMPTS
        + _PERSONA_TIMEOUT_MARGIN_SECONDS
    )


def _persona_failure_message(error: Exception) -> str:
    """Return a concise local-model error for the bounce-message writer."""

    settings = load_settings()
    if isinstance(error, TimeoutError):
        return (
            "Persona generation reached its overall bounded time limit across every "
            "attempt. The existing rejection notice was preserved; verify Ollama "
            "capacity and retry."
        )
    if "not found" in str(error).lower():
        return (
            f"Local model {settings.ollama_model!r} is not available in Ollama. Install that "
            "model or update CA_OLLAMA_MODEL, then retry."
        )
    return (
        "The local persona writer could not finish. The existing rejection notice was "
        "preserved. Confirm that the selected local model can answer a short prompt, then retry."
    )


def _draft_error_message(error: Exception) -> str:
    """Translate typed draft validation into concise operator guidance."""

    detail = str(error)
    if "rejection notice must not include the internal policy ID" in detail:
        return (
            "Remove the internal policy ID from the rejection notice. Senders should see only "
            "the broad bounce-message category."
        )
    return detail


class ConsoleState(rx.State):
    """All browser-visible state remains subordinate to typed server-side validation."""

    active_view: str = "new_policy"
    section: str = "compliance"
    rule_name: str = "Managed Gmail policy"
    ou_path: str = "/"
    inbound: bool = True
    outbound: bool = False
    internal_sending: bool = False
    internal_receiving: bool = False
    combiner: str = "any"
    expression_type: str = "advanced"
    location: str = "full_headers"
    match_type: str = "matches_regex"
    expression_value: str = ""
    regex_description: str = ""
    additional_expressions: list[dict[str, str]] = []  # noqa: RUF012
    metadata_attribute: str = "secure_transport"
    metadata_operator: str = "not_tls"
    predefined_detector: str = "Financial account number"
    required_capability: str = "dlp_predefined_detectors"
    minimum_match_count: int = 1
    predefined_confidence: str = "none"
    compliance_address_list_mode: str = "none"
    compliance_address_lists: str = ""
    sender_filter_enabled: bool = False
    sender_filter_selector: str = "single_address"
    sender_filter_value: str = ""
    recipient_filter_enabled: bool = False
    recipient_filter_selector: str = "single_address"
    recipient_filter_value: str = ""
    blocked_values: str = ""
    bypass_values: str = ""
    policy_category: str = "confidential-information"
    policy_id: str = "GW-1042"
    rejection_notice: str = _starter_notice()
    persona_role: str = _STARTER_PERSONA_ROLE
    persona_voice: str = _STARTER_PERSONA_VOICE
    persona_motif: str = _STARTER_PERSONA_MOTIF
    persona_seed: int = 0
    persona_traits: list[str] = list(_STARTER_PERSONA_TRAITS)  # noqa: RUF012
    persona_generated: bool = False
    persona_edited: bool = False
    persona_error: str = ""
    persona_history: list[str] = []  # noqa: RUF012
    expression_valid: bool = False
    validation_message: str = "Enter a match expression to continue"
    preview_ready: bool = False
    live_evidence_bound: bool = False
    approved: bool = False
    acknowledged: bool = False
    approval_phrase: str = ""
    phrase_entry: str = ""
    plan_hash: str = ""
    before_hash: str = "pending-live-read"
    change_hash: str = ""
    status: str = "Draft"
    status_tone: str = "draft"
    error_message: str = ""
    review_in_progress: bool = False
    persona_in_progress: bool = False
    agent_activity: list[dict[str, str]] = _idle_agent_activity()
    run_mode: str = "plan_only"
    expected_admin_email: str = ""
    workspace_domain: str = ""
    configuration_message: str = ""
    configuration_tone: str = "info"
    run_id: str = ""
    operation: str = "create"
    target_rule_id: str = ""
    browser_in_progress: bool = False
    execution_in_progress: bool = False
    before_summary: str = "No Google state has been read."
    after_summary: str = "Create a preview to calculate the desired state."
    change_summary: str = "Pending"
    run_history: list[dict[str, str]] = []  # noqa: RUF012
    managed_policies: list[dict[str, str]] = []  # noqa: RUF012
    audit_history: list[dict[str, str]] = []  # noqa: RUF012
    rule_enabled: bool = True
    model_label: str = "Gemma (local)"
    orchestration_model: str = "gemma4:12b"
    browser_model: str = "gemma4:12b"
    available_models: list[str] = ["gemma4:12b"]  # noqa: RUF012
    new_model_tag: str = ""
    model_catalog_in_progress: bool = False
    model_pull_in_progress: bool = False
    draft_revision: int = 0
    google_state_in_progress: bool = False
    google_state_error: str = ""
    google_state_surface_label: str = ""
    google_state_read_at: str = ""
    observed_google_rules: list[dict[str, str]] = []  # noqa: RUF012
    observed_unmanaged_rules: list[str] = []  # noqa: RUF012

    async def load_runtime_settings(self) -> AsyncIterator[None]:
        """Load the persisted mode and non-secret Google identity expectations."""

        settings = load_settings()
        self.run_mode = settings.run_mode.value
        self.expected_admin_email = settings.expected_admin_email
        self.workspace_domain = settings.expected_workspace_domain
        self.model_label = f"{settings.ollama_model} · local"
        self.orchestration_model = settings.ollama_model
        self.browser_model = settings.browser_model
        self._set_available_models(())
        self._load_managed_policies()
        self._load_audit_history()
        self.model_catalog_in_progress = True
        yield
        try:
            self._set_available_models(await list_local_models(settings))
        except RuntimeError as error:
            self.configuration_message = str(error)
            self.configuration_tone = "error"
        finally:
            self.model_catalog_in_progress = False

    @rx.var
    def run_mode_label(self) -> str:
        return {
            "plan_only": "Plan only",
            "dry_run": "Dry run",
            "live": "Live",
        }.get(self.run_mode, "Plan only")

    @rx.var
    def browser_mode(self) -> bool:
        return self.run_mode in {"dry_run", "live"}

    @rx.var
    def workflow_locked(self) -> bool:
        return self.review_in_progress or self.browser_in_progress or self.execution_in_progress

    @rx.var
    def model_controls_locked(self) -> bool:
        return self.workflow_locked or self.model_catalog_in_progress or self.model_pull_in_progress

    @rx.var
    def standard_ou_locked(self) -> bool:
        return self.section == "standard" and self.operation != "create"

    @rx.var
    def metadata_operator_label(self) -> str:
        return _METADATA_OPERATOR_LABELS.get(self.metadata_operator, self.metadata_operator)

    @rx.var
    def metadata_operator_options(self) -> list[str]:
        return [
            _METADATA_OPERATOR_LABELS[operator]
            for operator in _METADATA_OPERATORS[self.metadata_attribute]
        ]

    @rx.var
    def approval_state_label(self) -> str:
        if self.approval_ready:
            return "Approval ready"
        if self.live_evidence_bound and not self.acknowledged:
            return "Review acknowledgment required"
        if self.live_evidence_bound:
            return "Exact approval phrase required"
        if self.preview_ready and self.status == "No change":
            return "No change · approval not required"
        return (
            "Live approval locked" if self.run_mode == "live" else "No live approval in this mode"
        )

    @rx.var
    def approval_ready(self) -> bool:
        return (
            self.live_evidence_bound
            and self.acknowledged
            and bool(self.approval_phrase)
            and self.phrase_entry.strip() == self.approval_phrase
            and not self.execution_in_progress
        )

    @rx.var
    def live_mode(self) -> bool:
        return self.run_mode == "live"

    @rx.var
    def expression_type_label(self) -> str:
        return _EXPRESSION_TYPE_LABELS.get(self.expression_type, "Advanced")

    @rx.var
    def location_label(self) -> str:
        return _LOCATION_LABELS.get(self.location, "Full headers")

    @rx.var
    def match_type_label(self) -> str:
        return _MATCH_TYPE_LABELS.get(self.match_type, "Matches regex")

    @rx.var
    def combiner_label(self) -> str:
        return "Match ALL expressions" if self.combiner == "all" else "Match ANY expression"

    @rx.var
    def compliance_address_list_mode_label(self) -> str:
        return _ADDRESS_LIST_MODE_LABELS.get(
            self.compliance_address_list_mode,
            "No address-list condition",
        )

    @rx.var
    def sender_filter_selector_label(self) -> str:
        return _FILTER_SELECTOR_LABELS.get(self.sender_filter_selector, "Single address")

    @rx.var
    def recipient_filter_selector_label(self) -> str:
        return _FILTER_SELECTOR_LABELS.get(self.recipient_filter_selector, "Single address")

    @rx.var
    def metadata_attribute_label(self) -> str:
        return _METADATA_ATTRIBUTE_LABELS.get(
            self.metadata_attribute,
            "Secure transport (TLS)",
        )

    def set_expected_admin_email(self, value: str) -> None:
        self.expected_admin_email = value
        self._reset_preview_evidence(status="Unsaved configuration")

    def set_workspace_domain(self, value: str) -> None:
        self.workspace_domain = value
        self._reset_preview_evidence(status="Unsaved configuration")

    def set_orchestration_model(self, value: str) -> None:
        self.orchestration_model = value
        self._reset_preview_evidence(status="Unsaved configuration")

    def set_browser_model(self, value: str) -> None:
        self.browser_model = value
        self._reset_preview_evidence(status="Unsaved configuration")

    def set_new_model_tag(self, value: str) -> None:
        self.new_model_tag = value
        self.configuration_message = ""
        self.configuration_tone = "info"

    def set_rule_enabled(self, value: bool) -> None:
        self.rule_enabled = value
        self._mark_draft_changed()

    def start_create(self, section: str) -> None:
        """Reset managed identity and open a fresh create editor."""

        if self.workflow_locked:
            self.error_message = "Wait for the current review or browser step to finish."
            return
        self._reset_create_editor(section)
        self.section = section
        self.operation = "create"
        self.target_rule_id = ""
        self.rule_enabled = True
        self.active_view = "new_policy"
        self._reset_preview_evidence(status="Draft")

    def start_new_policy(self) -> None:
        """Open a truly fresh editor from the sidebar using the current surface."""

        self.start_create(self.section)

    def _reset_create_editor(self, section: str) -> None:
        self.rule_name = "Managed Gmail policy"
        self.ou_path = "/"
        self.rule_enabled = True
        self.blocked_values = ""
        self.bypass_values = ""
        self.rejection_notice = _starter_notice()
        self.persona_role = _STARTER_PERSONA_ROLE
        self.persona_voice = _STARTER_PERSONA_VOICE
        self.persona_motif = _STARTER_PERSONA_MOTIF
        self.persona_seed = 0
        self.persona_traits = list(_STARTER_PERSONA_TRAITS)
        self.persona_generated = False
        self.persona_edited = False
        self.persona_error = ""
        if section == "compliance":
            self.inbound = True
            self.outbound = False
            self.internal_sending = False
            self.internal_receiving = False
            self.combiner = "any"
            self.expression_type = "advanced"
            self.location = "full_headers"
            self.match_type = "matches_regex"
            self.expression_value = ""
            self.regex_description = ""
            self.expression_valid = False
            self.validation_message = "Enter a match expression to continue"
            self.additional_expressions = []
            self.minimum_match_count = 1
            self.compliance_address_list_mode = "none"
            self.compliance_address_lists = ""
            self.sender_filter_enabled = False
            self.sender_filter_value = ""
            self.recipient_filter_enabled = False
            self.recipient_filter_value = ""

    def edit_policy(self, surface: str, ownership_id: str) -> None:
        """Load one locally owned, last-verified policy into the typed editor."""

        if self.workflow_locked:
            self.error_message = "Wait for the current review or browser step to finish."
            return
        registry = OwnershipStore(load_settings().state_dir).load()
        identifier = UUID(ownership_id)
        if surface == "standard":
            record = registry.find(identifier)
            if (
                record is None
                or record.rule_snapshot is None
                or record.address_list_snapshot is None
            ):
                self.error_message = "Verified blocked-sender snapshot is unavailable."
                return
            self._load_standard_record(record, registry)
        else:
            record = registry.find_compliance(identifier)
            if record is None or record.rule_snapshot is None:
                self.error_message = "Verified Content compliance snapshot is unavailable."
                return
            self._load_compliance_record(record.rule_snapshot)
        self.operation = "update"
        self.target_rule_id = ownership_id
        self.active_view = "new_policy"
        self._reset_preview_evidence(status="Editing managed policy")

    def remove_policy(self, surface: str, ownership_id: str) -> None:
        """Load a managed policy into an explicit destructive review flow."""

        self.edit_policy(surface, ownership_id)
        if self.target_rule_id == ownership_id:
            self.operation = "remove"
            self.status = "Removal draft"

    def toggle_policy(self, surface: str, ownership_id: str) -> None:
        """Load and invert one managed policy's enabled state for exact preview."""

        self.edit_policy(surface, ownership_id)
        if self.target_rule_id == ownership_id:
            self.rule_enabled = not self.rule_enabled
            self.operation = "toggle"
            self.status = "Enabled-state draft"

    def change_run_mode(self, label: str) -> None:
        """Persist a UI-selected execution mode for the very next run."""

        if self.workflow_locked:
            self.configuration_message = "Wait for the current review or browser step to finish."
            self.configuration_tone = "error"
            return
        mode = {
            "Plan only": RunMode.PLAN_ONLY,
            "Dry run": RunMode.DRY_RUN,
            "Live": RunMode.LIVE,
        }.get(label)
        if mode is None:
            self.configuration_message = "Choose a recognized run mode."
            self.configuration_tone = "error"
            return
        if mode is RunMode.LIVE and (
            not self.expected_admin_email.strip() or not self.workspace_domain.strip()
        ):
            self.configuration_message = (
                "Save the expected administrator email and Workspace domain before live mode."
            )
            self.configuration_tone = "error"
            return
        LocalConfigurationStore(Path.cwd() / ".env").save_run_mode(mode)
        self.run_mode = mode.value
        self.configuration_message = (
            f"{label} mode is active for the next review. Existing approvals were cleared."
        )
        self.configuration_tone = "success"
        self._reset_preview_evidence(status="Mode changed · review again")

    def save_google_identities(self) -> None:
        """Validate and persist the identities checked during attended browser work."""

        try:
            email, domain = LocalConfigurationStore(Path.cwd() / ".env").save_google_identities(
                self.expected_admin_email,
                self.workspace_domain,
            )
        except ValueError as error:
            self.configuration_message = str(error)
            self.configuration_tone = "error"
            return
        self.expected_admin_email = email
        self.workspace_domain = domain
        self.configuration_message = "Google identity expectations saved locally."
        self.configuration_tone = "success"
        self._reset_preview_evidence(status="Identity settings changed · review again")

    def save_agent_models(self) -> None:
        """Persist the local group-chat and browser-vision model selections."""

        try:
            orchestration, browser = LocalConfigurationStore(Path.cwd() / ".env").save_agent_models(
                self.orchestration_model, self.browser_model
            )
        except ValueError as error:
            self.configuration_message = str(error)
            self.configuration_tone = "error"
            return
        self.orchestration_model = orchestration
        self.browser_model = browser
        self.model_label = f"{orchestration} · local"
        self.configuration_message = "Local agent model selections saved."
        self.configuration_tone = "success"
        self._reset_preview_evidence(status="Model settings changed · review again")

    async def refresh_local_models(self) -> AsyncIterator[None]:
        """Refresh installed Ollama choices without changing either selection."""

        if self.model_catalog_in_progress or self.model_pull_in_progress:
            return
        self.model_catalog_in_progress = True
        self.configuration_message = "Refreshing installed Ollama models…"
        self.configuration_tone = "info"
        yield
        try:
            self._set_available_models(await list_local_models(load_settings()))
        except RuntimeError as error:
            self.configuration_message = str(error)
            self.configuration_tone = "error"
        else:
            self.configuration_message = (
                f"Found {len(self.available_models)} installed Ollama models."
            )
            self.configuration_tone = "success"
        finally:
            self.model_catalog_in_progress = False

    async def add_local_model(self) -> AsyncIterator[None]:
        """Pull a new model locally, then expose it in both selection menus."""

        if self.model_catalog_in_progress or self.model_pull_in_progress:
            return
        requested = self.new_model_tag
        self.model_pull_in_progress = True
        self.configuration_message = "Adding the model through local Ollama…"
        self.configuration_tone = "info"
        yield
        try:
            settings = load_settings()
            added = await pull_local_model(settings, requested)
            self._set_available_models(await list_local_models(settings))
        except (RuntimeError, ValueError) as error:
            self.configuration_message = str(error)
            self.configuration_tone = "error"
        else:
            self.new_model_tag = ""
            self.configuration_message = (
                f"Added {added}. Choose where to use it, then save the selections."
            )
            self.configuration_tone = "success"
        finally:
            self.model_pull_in_progress = False

    def select_view(self, view: str) -> None:
        """Navigate among the operator surfaces without leaving the local console."""

        self.active_view = view

    def _set_available_models(self, models: tuple[str, ...]) -> None:
        choices = {
            model
            for model in (*models, self.orchestration_model, self.browser_model)
            if model.strip()
        }
        self.available_models = sorted(choices, key=str.casefold)

    def open_audit_folder(self, run_id: str) -> None:
        """Open one catalog-validated local audit directory for inspection."""

        summary = AuditCatalog(load_settings().audit_dir).find(run_id)
        if summary is None:
            self.configuration_message = "That audit package is no longer available."
            self.configuration_tone = "error"
            return
        if not webbrowser.open(summary.run_directory.as_uri()):
            self.configuration_message = f"Audit folder: {summary.run_directory}"
        else:
            self.configuration_message = "Opened the verified local audit folder."
        self.configuration_tone = "success"

    def export_audit_package(self, run_id: str) -> None:
        """Create a ZIP outside the immutable run directory without changing its manifest."""

        settings = load_settings()
        summary = AuditCatalog(settings.audit_dir).find(run_id)
        if summary is None:
            self.configuration_message = "That audit package is no longer available."
            self.configuration_tone = "error"
            return
        exports = settings.audit_dir / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        archive = Path(
            shutil.make_archive(
                str(exports / run_id),
                "zip",
                root_dir=summary.run_directory,
            )
        )
        self.configuration_message = f"Audit ZIP created: {archive}"
        self.configuration_tone = "success"

    def set_combiner(self, value: str) -> None:
        self.combiner = value
        self._mark_draft_changed()

    def set_combiner_label(self, value: str) -> None:
        self.combiner = "all" if value.startswith("Match ALL") else "any"
        self._mark_draft_changed()

    def set_location_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _LOCATION_LABELS.items()}
        self.location = reverse.get(value, "full_headers")
        self._mark_draft_changed()

    def set_match_type_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _MATCH_TYPE_LABELS.items()}
        self.match_type = reverse.get(value, "matches_regex")
        if self.match_type == "is_empty":
            self.expression_value = ""
        if self.match_type not in {"matches_regex", "not_matches_regex"}:
            self.regex_description = ""
            self.minimum_match_count = 1
        self._mark_draft_changed()

    def set_expression_type_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _EXPRESSION_TYPE_LABELS.items()}
        self.expression_type = reverse.get(value, "advanced")
        if self.expression_type == "metadata":
            self.metadata_operator = _METADATA_OPERATORS[self.metadata_attribute][0]
            if self.metadata_attribute not in {"source_ip", "message_size"}:
                self.expression_value = ""
        elif self.expression_type == "predefined":
            self.expression_value = ""
        self._mark_draft_changed()

    @rx.var
    def direction_summary(self) -> str:
        labels = [
            label
            for selected, label in (
                (self.inbound, "Inbound"),
                (self.outbound, "Outbound"),
                (self.internal_sending, "Internal-Sending"),
                (self.internal_receiving, "Internal-Receiving"),
            )
            if selected
        ]
        return ", ".join(labels) if labels else "None selected"

    @rx.var
    def expression_count(self) -> int:
        return 1 + len(self.additional_expressions)

    @rx.var
    def notice_character_count(self) -> int:
        return len(self.rejection_notice)

    @rx.var
    def persona_status_label(self) -> str:
        if self.persona_error:
            return "Generation failed · previous draft preserved"
        if self.persona_edited:
            return "Edited after generation" if self.persona_generated else "Manually edited draft"
        if self.persona_generated:
            return "Fresh model-generated profile"
        return "Starter draft · generate a persona"

    @rx.var
    def blocked_entry_count(self) -> int:
        return len([value for value in self.blocked_values.splitlines() if value.strip()])

    @rx.var
    def bypass_entry_count(self) -> int:
        return len([value for value in self.bypass_values.splitlines() if value.strip()])

    @rx.var
    def draft_minimum_ready(self) -> bool:  # noqa: C901, PLR0911 - mirrors typed surfaces.
        if self.persona_in_progress or len(self.rejection_notice) > _MAX_NOTICE_CHARACTERS:
            return False
        if self.browser_mode and (
            not self.expected_admin_email.strip() or not self.workspace_domain.strip()
        ):
            return False
        if self.operation in {"remove", "toggle"}:
            return bool(self.target_rule_id)
        if not self.ou_path.startswith("/") or not self.rejection_notice.strip():
            return False
        if self.section == "standard":
            return self.blocked_entry_count > 0
        if not any((self.inbound, self.outbound, self.internal_sending, self.internal_receiving)):
            return False
        if not self.policy_id.strip() or not self.policy_category.strip():
            return False
        try:
            if self.expression_type == "advanced" and self.match_type in {
                "matches_regex",
                "not_matches_regex",
            }:
                validate_google_regex(self.expression_value)
            self._build_expression()
            for row in self.additional_expressions:
                _expression_from_row(row)
        except (TypeError, ValueError):
            return False
        return True

    @rx.var
    def draft_readiness_message(self) -> str:  # noqa: C901, PLR0911 - readiness gates.
        if self.persona_in_progress:
            return "Wait for persona generation to finish."
        if len(self.rejection_notice) > _MAX_NOTICE_CHARACTERS:
            return "Shorten the rejection notice to 1,000 characters or fewer."
        if self.browser_mode and (
            not self.expected_admin_email.strip() or not self.workspace_domain.strip()
        ):
            return "Save the expected Google administrator and Workspace domain in Settings."
        if self.operation in {"remove", "toggle"} and not self.target_rule_id:
            return "Select one managed policy from Ownership first."
        if not self.ou_path.startswith("/"):
            return "Enter an absolute organizational-unit path beginning with /."
        if not self.rejection_notice.strip():
            return "Enter a rejection notice or generate a persona."
        if self.section == "standard" and self.blocked_entry_count == 0:
            return "Add at least one domain or email address to block."
        if self.section == "compliance" and not any(
            (self.inbound, self.outbound, self.internal_sending, self.internal_receiving)
        ):
            return "Select at least one email direction."
        if (
            self.section == "compliance"
            and not self.expression_value.strip()
            and (self.expression_type in {"advanced", "simple"} and self.match_type != "is_empty")
        ):
            return "Enter the first match expression."
        if self.section == "compliance" and any(
            not row.get("value", "").strip()
            and row.get("type") in {"advanced", "simple"}
            and row.get("match_type") != "is_empty"
            for row in self.additional_expressions
        ):
            return "Complete or remove every additional expression."
        return "Complete the required policy fields to continue."

    def add_expression(self) -> None:
        """Append a safe editable expression row to the draft."""

        if len(self.additional_expressions) >= _MAX_ADDITIONAL_EXPRESSIONS:
            self.error_message = "Google Content compliance supports at most ten expressions."
            return
        self.additional_expressions = [
            *self.additional_expressions,
            {
                "type": "advanced",
                "type_label": "Advanced",
                "location": "subject",
                "location_label": "Subject",
                "match_type": "contains",
                "match_type_label": "Contains",
                "value": "",
                "description": "",
                "minimum_match_count": "1",
                "attribute": "secure_transport",
                "operator": "not_tls",
                "detector": "Financial account number",
                "required_capability": "dlp_predefined_detectors",
                "confidence": "none",
            },
        ]
        self._mark_draft_changed()

    def remove_expression(self, index: int | str) -> None:
        """Remove one additional expression by its visible row index."""

        row_index = int(index)
        if 0 <= row_index < len(self.additional_expressions):
            self.additional_expressions = [
                row
                for current_index, row in enumerate(self.additional_expressions)
                if current_index != row_index
            ]
            self._mark_draft_changed()

    def update_expression(  # noqa: C901, PLR0912 - normalized row update boundary.
        self,
        index: int | str,
        field: str,
        value: str,
    ) -> None:
        """Persist one editable value from an additional expression row."""

        row_index = int(index)
        if not 0 <= row_index < len(self.additional_expressions):
            return
        normalized_value = value
        target_field = field
        if field == "type_label":
            target_field = "type"
            reverse = {label: raw for raw, label in _EXPRESSION_TYPE_LABELS.items()}
            normalized_value = reverse.get(value, "advanced")
        elif field == "location_label":
            target_field = "location"
            reverse = {label: raw for raw, label in _LOCATION_LABELS.items()}
            normalized_value = reverse.get(value, "subject")
        elif field == "match_type_label":
            target_field = "match_type"
            reverse = {label: raw for raw, label in _MATCH_TYPE_LABELS.items()}
            normalized_value = reverse.get(value, "contains")
        elif field == "type":
            normalized_value = value.casefold()
        elif field == "location":
            normalized_value = (
                value
                if value in _ADVANCED_LOCATIONS
                else {
                    "Full headers": "full_headers",
                    "Headers and body": "headers_and_body",
                    "Body": "body",
                    "Subject": "subject",
                    "Sender header": "sender_header",
                    "Recipient header": "recipient_header",
                    "Envelope sender": "envelope_sender",
                    "Envelope recipient": "envelope_recipient",
                    "Raw message": "raw_message",
                }.get(value, "subject")
            )
        elif field == "match_type":
            normalized_value = (
                value
                if value in _ADVANCED_MATCH_TYPES
                else {
                    "Matches regex": "matches_regex",
                    "Does not match regex": "not_matches_regex",
                    "Contains": "contains",
                    "Does not contain": "not_contains",
                    "Equals": "equals",
                    "Starts with": "starts_with",
                    "Ends with": "ends_with",
                    "Is empty": "is_empty",
                    "Matches any word": "matches_any_word",
                    "Matches all words": "matches_all_words",
                }.get(value, "contains")
            )

        updated_rows = [dict(row) for row in self.additional_expressions]
        updated_rows[row_index][target_field] = normalized_value
        if target_field == "type":
            updated_rows[row_index]["type_label"] = _EXPRESSION_TYPE_LABELS[normalized_value]
        elif target_field == "location":
            updated_rows[row_index]["location_label"] = _LOCATION_LABELS[normalized_value]
        elif target_field == "match_type":
            updated_rows[row_index]["match_type_label"] = _MATCH_TYPE_LABELS[normalized_value]
            if normalized_value == "is_empty":
                updated_rows[row_index]["value"] = ""
        elif target_field == "attribute" and normalized_value in _METADATA_OPERATORS:
            updated_rows[row_index]["operator"] = _METADATA_OPERATORS[normalized_value][0]
            if normalized_value not in {"source_ip", "message_size"}:
                updated_rows[row_index]["value"] = ""
        self.additional_expressions = updated_rows
        self._mark_draft_changed()

    def set_rule_name(self, value: str) -> None:
        self.rule_name = value
        self._mark_draft_changed()

    def set_ou_path(self, value: str) -> None:
        self.ou_path = value
        self._mark_draft_changed()

    def set_inbound(self, value: bool) -> None:
        self.inbound = value
        self._mark_draft_changed()

    def set_outbound(self, value: bool) -> None:
        self.outbound = value
        self._mark_draft_changed()

    def set_internal_sending(self, value: bool) -> None:
        self.internal_sending = value
        self._mark_draft_changed()

    def set_internal_receiving(self, value: bool) -> None:
        self.internal_receiving = value
        self._mark_draft_changed()

    def set_location(self, value: str) -> None:
        self.location = value
        self._mark_draft_changed()

    def set_expression_type(self, value: str) -> None:
        self.expression_type = value
        if value == "metadata":
            self.metadata_operator = _METADATA_OPERATORS[self.metadata_attribute][0]
            if self.metadata_attribute not in {"source_ip", "message_size"}:
                self.expression_value = ""
        elif value == "predefined":
            self.expression_value = ""
        self._mark_draft_changed()

    def set_match_type(self, value: str) -> None:
        self.match_type = value
        if value == "is_empty":
            self.expression_value = ""
        if value not in {"matches_regex", "not_matches_regex"}:
            self.regex_description = ""
            self.minimum_match_count = 1
        self._mark_draft_changed()

    def set_expression_value(self, value: str) -> None:
        self.expression_value = value
        self._mark_draft_changed()

    def set_regex_description(self, value: str) -> None:
        self.regex_description = value
        self._mark_draft_changed()

    def set_metadata_attribute(self, value: str) -> None:
        self.metadata_attribute = value
        self.metadata_operator = _METADATA_OPERATORS[value][0]
        if value not in {"source_ip", "message_size"}:
            self.expression_value = ""
        self._mark_draft_changed()

    def set_metadata_attribute_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _METADATA_ATTRIBUTE_LABELS.items()}
        self.metadata_attribute = reverse.get(value, "secure_transport")
        self.metadata_operator = _METADATA_OPERATORS[self.metadata_attribute][0]
        if self.metadata_attribute not in {"source_ip", "message_size"}:
            self.expression_value = ""
        self._mark_draft_changed()

    def set_metadata_operator(self, value: str) -> None:
        self.metadata_operator = value
        self._mark_draft_changed()

    def set_metadata_operator_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _METADATA_OPERATOR_LABELS.items()}
        operator = reverse.get(value)
        if operator in _METADATA_OPERATORS[self.metadata_attribute]:
            self.metadata_operator = operator
            self._mark_draft_changed()

    def set_predefined_detector(self, value: str) -> None:
        self.predefined_detector = value
        self._mark_draft_changed()

    def set_required_capability(self, value: str) -> None:
        self.required_capability = value
        self._mark_draft_changed()

    def set_minimum_match_count(self, value: int | str) -> None:
        self.minimum_match_count = max(1, int(value or 1))
        self._mark_draft_changed()

    def set_predefined_confidence(self, value: str) -> None:
        self.predefined_confidence = value
        self._mark_draft_changed()

    def set_compliance_address_list_mode(self, value: str) -> None:
        self.compliance_address_list_mode = value
        self._mark_draft_changed()

    def set_compliance_address_list_mode_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _ADDRESS_LIST_MODE_LABELS.items()}
        self.compliance_address_list_mode = reverse.get(value, "none")
        self._mark_draft_changed()

    def set_compliance_address_lists(self, value: str) -> None:
        self.compliance_address_lists = value
        self._mark_draft_changed()

    def set_sender_filter_enabled(self, value: bool) -> None:
        self.sender_filter_enabled = value
        self._mark_draft_changed()

    def set_sender_filter_selector(self, value: str) -> None:
        self.sender_filter_selector = value
        self._mark_draft_changed()

    def set_sender_filter_selector_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _FILTER_SELECTOR_LABELS.items()}
        self.sender_filter_selector = reverse.get(value, "single_address")
        self._mark_draft_changed()

    def set_sender_filter_value(self, value: str) -> None:
        self.sender_filter_value = value
        self._mark_draft_changed()

    def set_recipient_filter_enabled(self, value: bool) -> None:
        self.recipient_filter_enabled = value
        self._mark_draft_changed()

    def set_recipient_filter_selector(self, value: str) -> None:
        self.recipient_filter_selector = value
        self._mark_draft_changed()

    def set_recipient_filter_selector_label(self, value: str) -> None:
        reverse = {label: raw for raw, label in _FILTER_SELECTOR_LABELS.items()}
        self.recipient_filter_selector = reverse.get(value, "single_address")
        self._mark_draft_changed()

    def set_recipient_filter_value(self, value: str) -> None:
        self.recipient_filter_value = value
        self._mark_draft_changed()

    def set_blocked_values(self, value: str) -> None:
        self.blocked_values = value
        self._mark_draft_changed()

    def set_bypass_values(self, value: str) -> None:
        self.bypass_values = value
        self._mark_draft_changed()

    def set_policy_category(self, value: str) -> None:
        self.policy_category = value
        if not self.persona_generated and not self.persona_edited:
            self.rejection_notice = _starter_notice()
        self.persona_error = ""
        self._mark_draft_changed()

    def set_policy_id(self, value: str) -> None:
        self.policy_id = value
        self._mark_draft_changed()

    def set_rejection_notice(self, value: str) -> None:
        self.rejection_notice = value
        self.persona_edited = True
        self.persona_error = ""
        self._mark_draft_changed()

    def set_acknowledged(self, value: bool) -> None:
        self.acknowledged = value

    def set_phrase_entry(self, value: str) -> None:
        self.phrase_entry = value

    def select_section(self, section: str) -> None:
        """Switch between standard and advanced blocking editors."""

        if self.workflow_locked:
            self.error_message = "Wait for the current review or browser step to finish."
            return
        self.section = section
        self.operation = "create"
        self.target_rule_id = ""
        self.rule_enabled = True
        self._reset_preview_evidence(status="Draft")

    def validate_expression(self) -> None:
        """Validate the primary expression with the exact typed execution schema."""

        try:
            if self.expression_type == "advanced" and self.match_type in {
                "matches_regex",
                "not_matches_regex",
            }:
                validate_google_regex(self.expression_value)
            self._build_expression()
        except ValueError as error:
            self.expression_valid = False
            self.validation_message = str(error)
        else:
            self.expression_valid = True
            self.validation_message = {
                "advanced": (
                    "RE2 expression is valid"
                    if self.match_type in {"matches_regex", "not_matches_regex"}
                    else "Advanced match is valid"
                ),
                "simple": "Simple content match is valid",
                "metadata": "Metadata condition is valid",
                "predefined": "Predefined detector is configured",
            }[self.expression_type]

    async def generate_persona(self) -> AsyncIterator[None]:
        """Generate a fresh fictional persona and category-only bounce notice locally."""

        if self.persona_in_progress:
            return
        generation_revision = self.draft_revision
        generation_category = self.policy_category
        generation_policy_id = self.policy_id
        recent_signatures = tuple(self.persona_history)
        self.persona_in_progress = True
        self.status = "Generating persona"
        self.status_tone = "working"
        self.error_message = ""
        self.persona_error = ""
        yield
        try:
            settings = load_settings()
            async with asyncio.timeout(_persona_generation_budget_seconds(settings)):
                generated = await build_persona_generator(settings).generate(
                    policy_category=generation_category,
                    policy_id=generation_policy_id,
                    recent_profile_signatures=recent_signatures,
                )
        except Exception as error:
            _LOGGER.exception("Local persona generation failed")
            self.status = "Persona failed"
            self.status_tone = "error"
            self.persona_error = _persona_failure_message(error)
            self.persona_in_progress = False
            return
        if (
            self.draft_revision != generation_revision
            or self.policy_category != generation_category
            or self.policy_id != generation_policy_id
        ):
            self.status = "Persona result discarded"
            self.status_tone = "attention"
            self.persona_error = (
                "The draft changed while the persona was being generated. Your edits were "
                "preserved; generate again when ready."
            )
            self.persona_in_progress = False
            return
        self._apply_generated_notice(generated)
        self.persona_in_progress = False
        self.status = "Persona ready"
        self.status_tone = "ready"

    async def assess_google_state(self, surface: str) -> AsyncIterator[None]:
        """Read one surface's current Google Admin state through the attended browser."""

        if self.google_state_in_progress or self.workflow_locked:
            return
        self.google_state_error = ""
        settings = load_settings(run_mode=RunMode(self.run_mode))
        if settings.run_mode == RunMode.PLAN_ONLY:
            self.google_state_error = (
                "Plan-only mode never opens Google Admin. Switch to dry run or live in "
                "the top bar, then read the current state."
            )
            return
        plan = TaskPlan(
            status="plan",
            actions=(
                (ListBlockedSenderRules(),)
                if surface == "standard"
                else (ListContentComplianceRules(),)
            ),
        )
        self.google_state_in_progress = True
        self.browser_in_progress = True
        self.status = "Reading current Google state"
        self.status_tone = "working"
        yield
        try:
            await require_local_model(
                settings,
                settings.browser_model,
                require_vision=True,
            )
            preview = await ATTENDED_POLICY_SERVICE.preview(settings, plan)
        except Exception as error:
            _LOGGER.exception("Attended Google state read failed")
            self.google_state_error = _browser_failure_message(error)
            self.status = "State read blocked"
            self.status_tone = "error"
            self.google_state_in_progress = False
            self.browser_in_progress = False
            return
        self.google_state_in_progress = False
        self.browser_in_progress = False
        self._bind_observed_state(surface, preview)

    def _bind_observed_state(self, surface: str, preview: AttendedPolicyPreview) -> None:
        """Project one fresh read-only browser state into the operator console."""

        if surface == "standard":
            state = preview.standard_before
            label = "Blocked senders"
            rows = [
                {
                    "id": str(rule.ownership_id),
                    "surface": "standard",
                    "name": rule.display_name,
                    "enabled": "Enabled" if rule.enabled else "Disabled",
                    "detail": (
                        f"OU {rule.target_ou} · "
                        f"{len(rule.address_list_names)} blocked list(s) · "
                        f"{len(rule.bypass_address_list_names)} bypass list(s)"
                    ),
                }
                for rule in (state.rules if state is not None else ())
            ]
        else:
            state = preview.compliance_before
            label = "Content compliance"
            rows = [
                {
                    "id": str(rule.ownership_id),
                    "surface": "compliance",
                    "name": rule.display_name,
                    "enabled": "Enabled" if rule.enabled else "Disabled",
                    "detail": (
                        f"OU {rule.target_ou.path} · "
                        f"{len(rule.expressions)} expression(s) · "
                        f"{len(rule.directions)} direction(s)"
                    ),
                }
                for rule in (state.rules if state is not None else ())
            ]
        unmanaged = list(state.unmanaged_rule_names) if state is not None else []
        self.google_state_surface_label = label
        self.google_state_read_at = (
            datetime.now().astimezone().strftime("%b %d, %Y · %I:%M %p").lstrip("0")
        )
        self.observed_google_rules = rows
        self.observed_unmanaged_rules = unmanaged
        self.status = "Current Google state read"
        self.status_tone = "ready"
        self._record_run(
            "State read",
            f"{label}: {len(rows)} managed · {len(unmanaged)} unmanaged",
        )
        self._load_audit_history()

    async def preview(self) -> AsyncIterator[None]:  # noqa: PLR0911, PLR0915
        """Build the typed plan, then run the real bounded specialist group review."""

        if self.review_in_progress:
            return
        self.error_message = ""
        try:
            plan = self._build_plan()
        except (TypeError, ValueError) as error:
            self.preview_ready = False
            self.status = "Needs attention"
            self.status_tone = "error"
            self.error_message = _draft_error_message(error)
            return
        self._bind_draft(plan)
        review_revision = self.draft_revision
        self.review_in_progress = True
        self.status = "Agent review running"
        self.status_tone = "working"
        self.agent_activity = [
            {
                "name": spec.display_name,
                "time": "Reviewing",
                "icon": spec.icon,
                "status": "Reading the typed proposal and the other specialists' messages.",
                "findings": "",
            }
            for spec in PARTICIPANT_SPECS
        ]
        yield
        try:
            transcript = await _review_plan(plan)
        except Exception as error:
            _LOGGER.exception("Local specialist group review failed")
            failure_message = _review_failure_message(error)
            self._reset_preview_evidence(status="Agent review required")
            self.status_tone = "attention"
            self.error_message = failure_message
            self.agent_activity = [
                {
                    "name": spec.display_name,
                    "time": "Unavailable",
                    "icon": spec.icon,
                    "status": "No specialist output was accepted for this draft.",
                    "findings": "",
                }
                for spec in PARTICIPANT_SPECS
            ]
            self.review_in_progress = False
            return
        if self.draft_revision != review_revision:
            self.review_in_progress = False
            self._reset_preview_evidence(status="Draft changed during review")
            self.error_message = "The draft changed while agents were reviewing it. Review again."
            return
        reviewed_at = datetime.now().astimezone().strftime("%I:%M %p").lstrip("0")
        self.agent_activity = [
            {
                "name": message.display_name,
                "time": reviewed_at,
                "icon": message.icon,
                "status": message.text,
                "findings": "\n".join(f"• {finding}" for finding in message.findings),
            }
            for message in transcript.messages
        ]
        self.review_in_progress = False
        settings = load_settings(run_mode=RunMode(self.run_mode))
        self.run_mode = settings.run_mode.value
        if settings.run_mode == RunMode.PLAN_ONLY:
            self.run_id = ATTENDED_POLICY_SERVICE.record_plan_review(
                settings,
                plan,
                transcript,
            )
            self.status = "Plan ready"
            self.status_tone = "ready"
            self.before_summary = "Plan-only mode: Google Admin was not opened."
            self.after_summary = "Typed policy proposal is ready for review."
            self.change_summary = "Planned only"
            self._record_run("Plan ready", "No Google change")
            return
        self.browser_in_progress = True
        self.status = "Waiting for Google Admin login"
        self.status_tone = "working"
        yield
        try:
            await require_local_model(
                settings,
                settings.browser_model,
                require_vision=True,
            )
            attended = await ATTENDED_POLICY_SERVICE.preview(settings, plan, transcript)
        except Exception as error:
            _LOGGER.exception("Attended Google Admin preview failed")
            self.browser_in_progress = False
            self.status = "Browser preview blocked"
            self.status_tone = "error"
            self.error_message = _browser_failure_message(error)
            self._record_run("Preview blocked", self.error_message)
            return
        if self.draft_revision != review_revision:
            ATTENDED_POLICY_SERVICE.cancel(attended.run_id)
            self.browser_in_progress = False
            self._reset_preview_evidence(status="Draft changed during browser read")
            self.error_message = "The draft changed while Google state was read. Preview again."
            return
        self.browser_in_progress = False
        self._bind_attended_preview(attended)

    def _bind_draft(self, plan: TaskPlan) -> None:
        """Bind non-authoritative draft evidence before any live browser read."""

        self._clear_approval()
        self.plan_hash = canonical_hash(plan)
        self.before_hash = "pending-live-read"
        self.change_hash = "pending-live-diff"
        self.live_evidence_bound = False
        self.approval_phrase = f"APPLY {self.plan_hash[:4].upper()}"
        self.phrase_entry = ""
        self.acknowledged = False
        self.preview_ready = True
        self.approved = False

    def bind_live_evidence(self, before_hash: str, change_hash: str) -> None:
        """Bind trusted hashes returned by the fresh browser-read preview service."""

        if len(before_hash) != _SHA256_HEX_LENGTH or len(change_hash) != _SHA256_HEX_LENGTH:
            message = "live evidence hashes must be 64-character SHA-256 values"
            raise ValueError(message)
        self.before_hash = before_hash
        self.change_hash = change_hash
        self.live_evidence_bound = True
        self.status = "Awaiting approval"
        self.status_tone = "attention"

    async def approve_plan(self) -> AsyncIterator[None]:  # noqa: PLR0911, PLR0915
        """Consume one exact approval, execute, and independently verify Google state."""

        if not self.preview_ready:
            self.error_message = "Create a fresh preview before approval."
            return
        if not self.live_evidence_bound:
            self.approved = False
            self.status = "Live read required"
            self.status_tone = "attention"
            self.error_message = (
                "Approval is locked until the headed browser reads the current Google "
                "Admin state and binds its before-state and change-set hashes."
            )
            return
        if not self.acknowledged or self.phrase_entry.strip() != self.approval_phrase:
            self.error_message = "Review the preview and type the exact approval phrase."
            return
        if not self.run_id:
            self.error_message = "Create a fresh live preview before approval."
            return
        self.execution_in_progress = True
        self.status = "Applying approved change"
        self.status_tone = "working"
        self.error_message = ""
        yield
        try:
            result = await ATTENDED_POLICY_SERVICE.execute(
                self.run_id,
                phrase=self.phrase_entry,
                acknowledged=self.acknowledged,
            )
        except Exception:
            _LOGGER.exception("Attended Google Admin execution failed")
            self.execution_in_progress = False
            self._clear_approval()
            self.preview_ready = False
            self.status = "Execution blocked"
            self.status_tone = "error"
            self.error_message = (
                "The one-time approval was consumed. Review Audits for the terminal "
                "failed-unchanged or indeterminate outcome before retrying."
            )
            self._record_run("Execution blocked", self.error_message)
            self._load_audit_history()
            return
        self.execution_in_progress = False
        if result.status == "recovery_required":
            self._clear_approval()
            self.preview_ready = False
            self.live_evidence_bound = False
            self.status = "Applied · recovery required"
            self.status_tone = "error"
            self.error_message = (
                "Google read-back completed, but local ownership or terminal audit persistence "
                "needs recovery. Do not retry the change; inspect Ownership and Audits."
            )
            self._record_run(self.status, "Manual recovery required")
            self._load_audit_history()
            return
        if result.status == "drifted":
            self.preview_ready = False
            self.live_evidence_bound = False
            self.status = "Google state changed · review again"
            self.status_tone = "attention"
            self.error_message = "The before-state changed. Run Review and preview again."
            self._record_run("Drift detected", "Fresh approval required")
            self._load_audit_history()
            return
        self.approved = result.verified
        self.preview_ready = False
        self.live_evidence_bound = False
        self.status = "Applied and verified" if result.verified else "Verification failed"
        self.status_tone = "ready" if result.verified else "error"
        self.error_message = (
            "" if result.verified else "Google did not read back the complete expected state."
        )
        self.change_summary = result.status.replace("_", " ").title()
        if result.verified:
            self._load_managed_policies()
        self._record_run(self.status, self.change_summary)
        self._load_audit_history()

    def _bind_attended_preview(self, preview: AttendedPolicyPreview) -> None:
        self.run_id = preview.run_id
        self.plan_hash = preview.plan_hash
        self.before_hash = preview.before_state_hash
        self.change_hash = preview.change_set_hash
        self.approval_phrase = preview.approval_phrase or ""
        self.phrase_entry = ""
        self.acknowledged = False
        self.live_evidence_bound = preview.mode == RunMode.LIVE and preview.has_mutations
        self.preview_ready = True
        self.approved = False
        self.before_summary, self.after_summary, self.change_summary = _preview_summaries(preview)
        if not preview.has_mutations:
            self.status = "No change"
            self.status_tone = "ready"
        elif preview.mode == RunMode.DRY_RUN:
            self.status = "Dry-run preview ready"
            self.status_tone = "ready"
        else:
            self.status = "Awaiting exact approval"
            self.status_tone = "attention"
        self._record_run(self.status, self.change_summary)
        self._load_audit_history()

    def _clear_approval(self) -> None:
        if self.run_id:
            ATTENDED_POLICY_SERVICE.cancel(self.run_id)
        self.run_id = ""
        self.live_evidence_bound = False
        self.approved = False
        self.acknowledged = False
        self.phrase_entry = ""
        self.approval_phrase = ""

    def _mark_draft_changed(self) -> None:
        """Make stale preview evidence unusable after any operator edit."""

        self._reset_preview_evidence(status="Draft updated")
        self.before_summary = "Draft changed; Google state must be read again."
        self.after_summary = "Create a fresh preview for this edited policy."
        self.change_summary = "Pending fresh review"

    def _reset_preview_evidence(self, *, status: str) -> None:
        """Clear every review artifact whenever the visible draft context changes."""

        self.draft_revision += 1
        self._clear_approval()
        self.preview_ready = False
        self.plan_hash = ""
        self.before_hash = "pending-live-read"
        self.change_hash = ""
        self.before_summary = "No current Google evidence is bound."
        self.after_summary = "Review the current draft to calculate expected state."
        self.change_summary = "Pending fresh review"
        self.status = status
        self.status_tone = "draft"
        self.error_message = ""
        self.agent_activity = _idle_agent_activity()

    def _record_run(self, status: str, detail: str) -> None:
        self.run_history = [
            {
                "run_id": self.run_id[:8].upper() if self.run_id else "DRAFT",
                "surface": self.section.replace("_", " ").title(),
                "mode": self.run_mode.replace("_", " ").title(),
                "status": status,
                "detail": detail,
                "time": datetime.now().astimezone().strftime("%I:%M:%S %p").lstrip("0"),
            },
            *self.run_history,
        ][:25]

    def _load_managed_policies(self) -> None:
        registry = OwnershipStore(load_settings().state_dir).load()
        policies = [
            {
                "id": str(record.ownership_id),
                "surface": "standard",
                "surface_label": "Blocked senders",
                "name": record.rule_display_name,
                "ou": record.target_ou,
                "enabled": (
                    "Enabled"
                    if record.rule_snapshot is None or record.rule_snapshot.enabled
                    else "Disabled"
                ),
            }
            for record in registry.resources
        ]
        policies.extend(
            {
                "id": str(record.ownership_id),
                "surface": "compliance",
                "surface_label": "Content compliance",
                "name": record.display_name,
                "ou": record.target_ou,
                "enabled": (
                    "Enabled"
                    if record.rule_snapshot is None or record.rule_snapshot.enabled
                    else "Disabled"
                ),
            }
            for record in registry.compliance_rules
        )
        self.managed_policies = sorted(policies, key=lambda item: item["name"].casefold())

    def _load_audit_history(self) -> None:
        """Project verified terminal audit manifests into the operator console."""

        try:
            summaries = AuditCatalog(load_settings().audit_dir).list_runs()
        except OSError as error:
            self.audit_history = [
                {
                    "run_id": "UNAVAILABLE",
                    "full_id": "",
                    "started": "Audit directory could not be read",
                    "status": "Unavailable",
                    "integrity": str(error),
                }
            ]
            return
        self.audit_history = [
            {
                "run_id": summary.run_id[:8].upper(),
                "full_id": summary.run_id,
                "started": summary.started_at.astimezone().strftime("%b %d, %Y · %I:%M %p"),
                "status": summary.status.value.replace("_", " ").title(),
                "integrity": (
                    "Integrity verified"
                    if summary.integrity_valid
                    else f"Integrity warning · {len(summary.integrity_errors)} issue(s)"
                ),
            }
            for summary in summaries[:50]
        ]

    def _load_standard_record(self, record: object, registry: object) -> None:
        rule = record.rule_snapshot
        primary = record.address_list_snapshot
        if rule is None or primary is None:
            self.error_message = "Verified blocked-sender resource snapshots are incomplete."
            return
        bypass_entries: tuple[AddressEntry, ...] = ()
        for bypass_record in registry.address_lists:
            snapshot = bypass_record.address_list_snapshot
            if snapshot is not None and snapshot.display_name in rule.bypass_address_list_names:
                bypass_entries += snapshot.entries
        self.section = "standard"
        self.rule_name = rule.display_name
        self.ou_path = rule.target_ou
        self.blocked_values = "\n".join(entry.value for entry in primary.entries)
        self.bypass_values = "\n".join(entry.value for entry in bypass_entries)
        self.rejection_notice = rule.rejection_notice or self.rejection_notice
        self.persona_role = _STARTER_PERSONA_ROLE
        self.persona_voice = _STARTER_PERSONA_VOICE
        self.persona_motif = _STARTER_PERSONA_MOTIF
        self.persona_seed = 0
        self.persona_traits = list(_STARTER_PERSONA_TRAITS)
        self.persona_generated = False
        self.persona_edited = bool(rule.rejection_notice)
        self.persona_error = ""
        self.rule_enabled = rule.enabled

    def _load_compliance_record(self, rule: object) -> None:
        self.section = "compliance"
        self.rule_name = rule.display_name
        self.ou_path = rule.target_ou.path
        directions = set(rule.directions)
        self.inbound = MessageDirection.INBOUND in directions
        self.outbound = MessageDirection.OUTBOUND in directions
        self.internal_sending = MessageDirection.INTERNAL_SENDING in directions
        self.internal_receiving = MessageDirection.INTERNAL_RECEIVING in directions
        self.combiner = rule.combiner.value
        self._load_primary_expression(rule.expressions[0])
        self.additional_expressions = [
            _expression_row(expression) for expression in rule.expressions[1:]
        ]
        notice = rule.rejection_notice
        self.rejection_notice = notice.text
        self.policy_category = notice.policy_category
        self.policy_id = notice.policy_id
        self.persona_role = notice.persona.fictional_role
        self.persona_voice = notice.persona.voice
        self.persona_motif = notice.persona.motif
        self.persona_seed = notice.persona.seed
        self.persona_traits = list(notice.persona.traits)
        self.persona_generated = True
        self.persona_edited = False
        self.persona_error = ""
        signature = profile_signature(notice)
        self.persona_history = [*self.persona_history, signature][-_PERSONA_HISTORY_LIMIT:]
        self.rule_enabled = rule.enabled
        condition = rule.address_list_condition
        self.compliance_address_list_mode = condition.mode if condition is not None else "none"
        self.compliance_address_lists = (
            "\n".join(condition.address_list_names) if condition is not None else ""
        )
        sender = next(
            (item for item in rule.envelope_filters if item.party == "sender"),
            None,
        )
        recipient = next(
            (item for item in rule.envelope_filters if item.party == "recipient"),
            None,
        )
        self.sender_filter_enabled = sender is not None
        self.sender_filter_selector = sender.selector if sender else "single_address"
        self.sender_filter_value = sender.value if sender else ""
        self.recipient_filter_enabled = recipient is not None
        self.recipient_filter_selector = recipient.selector if recipient else "single_address"
        self.recipient_filter_value = recipient.value if recipient else ""

    def _load_primary_expression(self, expression: ComplianceExpression) -> None:
        data = expression.model_dump(mode="json")
        self.expression_type = data["type"]
        self.location = data.get("location", self.location)
        self.match_type = data.get("match_type", self.match_type)
        self.expression_value = data.get("value") or data.get("content") or ""
        self.regex_description = data.get("regex_description") or ""
        self.metadata_attribute = data.get("attribute", self.metadata_attribute)
        self.metadata_operator = data.get("operator", self.metadata_operator)
        self.predefined_detector = data.get("detector", self.predefined_detector)
        self.required_capability = data.get(
            "required_edition_capability",
            self.required_capability,
        )
        self.minimum_match_count = int(data.get("minimum_match_count", 1))
        self.predefined_confidence = data.get("confidence") or "none"

    def _apply_generated_notice(self, generated: GeneratedRejectionNotice) -> None:
        self._mark_draft_changed()
        self.rejection_notice = generated.text
        self.persona_role = generated.persona.fictional_role
        self.persona_voice = generated.persona.voice
        self.persona_motif = generated.persona.motif
        self.persona_seed = generated.persona.seed
        self.persona_traits = list(generated.persona.traits)
        self.persona_generated = True
        self.persona_edited = False
        self.persona_error = ""
        signature = profile_signature(generated)
        self.persona_history = [*self.persona_history, signature][-_PERSONA_HISTORY_LIMIT:]

    def _build_plan(self) -> TaskPlan:  # noqa: PLR0911 - typed operation dispatch is explicit.
        if self.section == "standard":
            if self.operation == "remove":
                return TaskPlan(
                    status="plan",
                    actions=(
                        RemoveBlockedSenderRule(
                            target_rule_id=UUID(self.target_rule_id),
                            remove_owned_address_list=True,
                        ),
                    ),
                )
            entries = tuple(
                _parse_address_entry(value) for value in self.blocked_values.splitlines()
            )
            entries = tuple(entry for entry in entries if entry is not None)
            if not entries:
                message = "Add at least one domain or email address."
                raise ValueError(message)
            bypass_entries = tuple(
                _parse_address_entry(value) for value in self.bypass_values.splitlines()
            )
            bypass_entries = tuple(entry for entry in bypass_entries if entry is not None)
            if self.operation in {"update", "toggle"}:
                if self.operation == "toggle":
                    return TaskPlan(
                        status="plan",
                        actions=(
                            SetBlockedSenderRuleEnabled(
                                target_rule_id=UUID(self.target_rule_id),
                                enabled=self.rule_enabled,
                            ),
                        ),
                    )
                return TaskPlan(
                    status="plan",
                    actions=(
                        UpdateBlockedSenderRule(
                            target_rule_id=UUID(self.target_rule_id),
                            entries=entries,
                            rejection_notice=self.rejection_notice,
                            bypass_entries=bypass_entries,
                            enabled=self.rule_enabled,
                        ),
                    ),
                )
            return TaskPlan.model_validate(
                {
                    "status": "plan",
                    "actions": [
                        {
                            "type": "create_blocked_sender_rule",
                            "entries": entries,
                            "target_ou": self.ou_path,
                            "rejection_notice": self.rejection_notice,
                            "bypass_entries": bypass_entries,
                            "enabled": self.rule_enabled,
                        }
                    ],
                }
            )
        if self.operation == "remove":
            return TaskPlan(
                status="plan",
                actions=(RemoveContentComplianceRule(target_rule_id=UUID(self.target_rule_id)),),
            )
        if self.operation == "toggle":
            return TaskPlan(
                status="plan",
                actions=(
                    SetContentComplianceRuleEnabled(
                        target_rule_id=UUID(self.target_rule_id),
                        enabled=self.rule_enabled,
                    ),
                ),
            )
        self.validate_expression()
        if not self.expression_valid:
            raise ValueError(self.validation_message)
        directions = tuple(
            direction
            for selected, direction in (
                (self.inbound, MessageDirection.INBOUND),
                (self.outbound, MessageDirection.OUTBOUND),
                (self.internal_sending, MessageDirection.INTERNAL_SENDING),
                (self.internal_receiving, MessageDirection.INTERNAL_RECEIVING),
            )
            if selected
        )
        persona = PersonaProfile(
            fictional_role=self.persona_role,
            traits=tuple(self.persona_traits),
            voice=self.persona_voice,
            motif=self.persona_motif,
            seed=self.persona_seed,
        )
        notice = GeneratedRejectionNotice(
            text=self.rejection_notice,
            policy_category=self.policy_category,
            policy_id=self.policy_id,
            persona=persona,
        )
        expressions = (
            self._build_expression(),
            *tuple(_expression_from_row(row) for row in self.additional_expressions),
        )
        address_list_names = tuple(
            name.strip() for name in self.compliance_address_lists.splitlines() if name.strip()
        )
        address_list_condition = (
            AddressListCondition(
                mode=self.compliance_address_list_mode,
                address_list_names=address_list_names,
            )
            if self.compliance_address_list_mode != "none"
            else None
        )
        envelope_filters = tuple(
            item
            for item in (
                EnvelopeFilter(
                    party="sender",
                    selector=self.sender_filter_selector,
                    value=self.sender_filter_value,
                )
                if self.sender_filter_enabled
                else None,
                EnvelopeFilter(
                    party="recipient",
                    selector=self.recipient_filter_selector,
                    value=self.recipient_filter_value,
                )
                if self.recipient_filter_enabled
                else None,
            )
            if item is not None
        )
        draft = ContentComplianceRuleDraft(
            target_ou=OrganizationalUnitRef(path=self.ou_path),
            directions=directions,
            combiner=ExpressionCombiner(self.combiner),
            expressions=expressions,
            rejection_notice=notice,
            address_list_condition=address_list_condition,
            envelope_filters=envelope_filters,
            enabled=self.rule_enabled,
        )
        if self.operation == "update":
            identifier = UUID(self.target_rule_id)
            managed = ManagedContentComplianceRule(
                **draft.model_dump(),
                ownership_id=identifier,
                display_name=self.rule_name,
            )
            return TaskPlan(
                status="plan",
                actions=(
                    UpdateContentComplianceRule(
                        target_rule_id=identifier,
                        rule=managed,
                    ),
                ),
            )
        return TaskPlan(
            status="plan",
            actions=(CreateContentComplianceRule(rule=draft),),
        )

    def _build_expression(self) -> ComplianceExpression:
        if self.expression_type == "simple":
            return SimpleContentMatch(content=self.expression_value)
        if self.expression_type == "metadata":
            attributes_requiring_value = {"source_ip", "message_size"}
            return MetadataMatch(
                attribute=MetadataAttribute(self.metadata_attribute),
                operator=self.metadata_operator,
                value=(
                    self.expression_value
                    if self.metadata_attribute in attributes_requiring_value
                    else None
                ),
            )
        if self.expression_type == "predefined":
            return PredefinedContentMatch(
                detector=self.predefined_detector,
                minimum_match_count=self.minimum_match_count,
                confidence=(
                    None if self.predefined_confidence == "none" else self.predefined_confidence
                ),
                required_edition_capability=self.required_capability,
            )
        return AdvancedContentMatch(
            location=AdvancedContentLocation(self.location),
            match_type=AdvancedMatchType(self.match_type),
            value=None if self.match_type == "is_empty" else self.expression_value,
            regex_description=(
                self.regex_description
                if self.match_type in {"matches_regex", "not_matches_regex"}
                else None
            ),
            minimum_match_count=(
                self.minimum_match_count
                if self.match_type in {"matches_regex", "not_matches_regex"}
                else 1
            ),
        )


def _parse_address_entry(raw: str) -> AddressEntry | None:
    value = raw.strip()
    if not value:
        return None
    kind = "email" if "@" in value else "domain"
    return AddressEntry(kind=kind, value=value)


def _browser_failure_message(error: Exception) -> str:
    """Translate attended browser failures without exposing credentials or page content."""

    text = str(error).strip()
    lowered = text.casefold()
    if isinstance(error, TimeoutError):
        return "Google Admin login or browser navigation reached its attended time limit."
    if "identity" in lowered or "domain" in lowered or "administrator" in lowered:
        return text
    if "model" in lowered or "ollama" in lowered or "connection" in lowered:
        return (
            "The local browser model was unavailable. Verify Ollama and CA_BROWSER_MODEL, "
            "then create a fresh preview."
        )
    if "requires operator clarification" in str(error).casefold():
        return (
            "At least one specialist requested clarification or marked the proposal unsafe. "
            "Refine the typed policy and run the complete group review again."
        )
    if "ownership" in lowered or "snapshot" in lowered:
        return text
    return (
        "The attended Google Admin run stopped safely before authorization could continue. "
        "Review the visible browser window, local model availability, and configured identities."
    )


def _preview_summaries(preview: AttendedPolicyPreview) -> tuple[str, str, str]:
    if preview.standard_change is not None:
        change = preview.standard_change
        before = preview.standard_before
        after = preview.standard_after
        if before is None or after is None:
            message = "standard preview omitted state evidence"
            raise ValueError(message)
        detail = _resource_change_summary(
            (
                ("Create rule", change.rules_to_create),
                ("Update rule", change.rules_to_update),
                ("Remove rule", change.rules_to_remove),
                ("Create list", change.address_lists_to_create),
                ("Update list", change.address_lists_to_update),
                ("Remove list", change.address_lists_to_remove),
            )
        )
        return (
            _standard_state_summary(before),
            _standard_state_summary(after),
            detail,
        )
    change = preview.compliance_change
    before = preview.compliance_before
    after = preview.compliance_after
    if change is None or before is None or after is None:
        message = "Content compliance preview omitted state evidence"
        raise ValueError(message)
    detail = _resource_change_summary(
        (
            ("Create rule", change.rules_to_create),
            ("Update rule", change.rules_to_update),
            ("Remove rule", change.rules_to_remove),
        )
    )
    return (
        _compliance_state_summary(before),
        _compliance_state_summary(after),
        detail,
    )


def _resource_change_summary(groups: tuple[tuple[str, tuple[object, ...]], ...]) -> str:
    changes = [
        f"{verb}: {getattr(resource, 'display_name', 'managed resource')}"
        for verb, resources in groups
        for resource in resources
    ]
    return "; ".join(changes) if changes else "No fields change"


def _standard_state_summary(state: object) -> str:
    rules = getattr(state, "rules", ())
    if not rules:
        return "No managed blocked-sender rules in this OU"
    return "; ".join(
        f"{rule.display_name} ({'enabled' if rule.enabled else 'disabled'})" for rule in rules
    )


def _compliance_state_summary(state: object) -> str:
    rules = getattr(state, "rules", ())
    if not rules:
        return "No managed Content compliance rules"
    return "; ".join(
        f"{rule.display_name} ({'enabled' if rule.enabled else 'disabled'}, "
        f"{len(rule.expressions)} expression(s))"
        for rule in rules
    )


def _expression_from_row(row: dict[str, str]) -> ComplianceExpression:
    expression_type = row.get("type", "advanced")
    value = row.get("value", "").strip()
    if expression_type == "simple":
        return SimpleContentMatch(content=value)
    if expression_type == "metadata":
        attribute = MetadataAttribute(row.get("attribute", "secure_transport"))
        return MetadataMatch(
            attribute=attribute,
            operator=row.get("operator", "not_tls"),
            value=value
            if attribute in {MetadataAttribute.SOURCE_IP, MetadataAttribute.MESSAGE_SIZE}
            else None,
        )
    if expression_type == "predefined":
        return PredefinedContentMatch(
            detector=row.get("detector", "Financial account number"),
            minimum_match_count=int(row.get("minimum_match_count", "1")),
            confidence=(None if row.get("confidence", "none") == "none" else row["confidence"]),
            required_edition_capability=row.get("required_capability", "dlp_predefined_detectors"),
        )
    match_type = AdvancedMatchType(row.get("match_type", "contains"))
    if match_type in {AdvancedMatchType.MATCHES_REGEX, AdvancedMatchType.NOT_MATCHES_REGEX}:
        validate_google_regex(value)
    return AdvancedContentMatch(
        location=AdvancedContentLocation(row.get("location", "subject")),
        match_type=match_type,
        value=None if match_type is AdvancedMatchType.IS_EMPTY else value,
        regex_description=(
            row.get("description", "Additional regex expression")
            if match_type in {AdvancedMatchType.MATCHES_REGEX, AdvancedMatchType.NOT_MATCHES_REGEX}
            else None
        ),
        minimum_match_count=(
            int(row.get("minimum_match_count", "1"))
            if match_type in {AdvancedMatchType.MATCHES_REGEX, AdvancedMatchType.NOT_MATCHES_REGEX}
            else 1
        ),
    )


def _expression_row(expression: ComplianceExpression) -> dict[str, str]:
    data = expression.model_dump(mode="json")
    return {
        "type": data["type"],
        "type_label": _EXPRESSION_TYPE_LABELS[data["type"]],
        "location": data.get("location", "subject"),
        "location_label": _LOCATION_LABELS[data.get("location", "subject")],
        "match_type": data.get("match_type", "contains"),
        "match_type_label": _MATCH_TYPE_LABELS[data.get("match_type", "contains")],
        "value": data.get("value") or data.get("content") or "",
        "description": data.get("regex_description") or "Managed expression",
        "minimum_match_count": str(data.get("minimum_match_count", 1)),
        "attribute": data.get("attribute", "secure_transport"),
        "operator": data.get("operator", "not_tls"),
        "detector": data.get("detector", "Financial account number"),
        "required_capability": data.get(
            "required_edition_capability",
            "dlp_predefined_detectors",
        ),
        "confidence": data.get("confidence") or "none",
    }
