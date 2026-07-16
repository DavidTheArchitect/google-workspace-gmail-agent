"""Server-owned Reflex state for policy composition and exact approval."""

from __future__ import annotations

import reflex as rx

from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.regex_validation import validate_google_regex
from compliance_agent.llm.planner import build_persona_generator
from compliance_agent.schemas.compliance import (
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    ComplianceExpression,
    ContentComplianceRuleDraft,
    ExpressionCombiner,
    GeneratedRejectionNotice,
    MessageDirection,
    MetadataAttribute,
    MetadataMatch,
    OrganizationalUnitRef,
    PersonaProfile,
    PredefinedContentMatch,
    SimpleContentMatch,
)
from compliance_agent.schemas.plan import CreateContentComplianceRule, TaskPlan
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.settings import load_settings

_SHA256_HEX_LENGTH = 64


class ConsoleState(rx.State):
    """All browser-visible state remains subordinate to typed server-side validation."""

    active_view: str = "new_policy"
    section: str = "compliance"
    rule_name: str = "Finance confidential marker guard"
    ou_path: str = "/Finance"
    inbound: bool = True
    outbound: bool = True
    internal_sending: bool = True
    internal_receiving: bool = True
    combiner: str = "any"
    expression_type: str = "advanced"
    location: str = "full_headers"
    match_type: str = "matches_regex"
    expression_value: str = r"(?i)(confidential|internal|yes only|for eyes only)"
    regex_description: str = "Reject messages carrying confidential finance markers"
    additional_expressions: list[dict[str, str]] = []  # noqa: RUF012
    metadata_attribute: str = "secure_transport"
    metadata_operator: str = "not_tls"
    predefined_detector: str = "Financial account number"
    required_capability: str = "dlp_predefined_detectors"
    blocked_values: str = "example-spam.test\nbot@example-spam.test"
    bypass_values: str = ""
    policy_category: str = "confidential-information"
    policy_id: str = "GW-1042"
    rejection_notice: str = (
        "Nice try! Your message didn't make it past our content bouncers.\n\n"
        "We protect sensitive info like a dragon guards its treasure.\n\n"
        "Check your message and try again. If you think this is a mistake, contact your IT "
        "admin with Policy ID GW-1042.\n\n"
        "Thanks for helping us keep things safe!\n— The Gmail Guardians"
    )
    persona_role: str = "Gmail Guardian"
    persona_voice: str = "playful, protective, and professional"
    persona_motif: str = "dragons and content bouncers"
    persona_seed: int = 1042
    persona_traits: list[str] = ["protective", "clear", "playful"]  # noqa: RUF012
    expression_valid: bool = True
    validation_message: str = "RE2 expression is valid"
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
    agent_activity: list[dict[str, str]] = [  # noqa: RUF012
        {
            "name": "Request Analyst",
            "time": "10:21 AM",
            "icon": "chart-no-axes-column-increasing",
            "status": (
                "Request received to block messages containing confidential markers "
                "in full headers for /Finance."
            ),
        },
        {
            "name": "Policy Architect",
            "time": "10:22 AM",
            "icon": "network",
            "status": (
                "Built a content compliance rule using full-headers regex across all four "
                "message directions. Combiner: Match ANY."
            ),
        },
        {
            "name": "Persona Writer",
            "time": "10:23 AM",
            "icon": "pen-line",
            "status": (
                "Crafted a playful, professional rejection notice with Policy ID GW-1042 "
                "for user clarity."
            ),
        },
        {
            "name": "Red-Team Reviewer",
            "time": "10:24 AM",
            "icon": "shield-check",
            "status": (
                "Reviewed bypass risk and scope creep. Regex is RE2-compatible and approval "
                "remains hash-bound."
            ),
        },
    ]

    def select_view(self, view: str) -> None:
        """Navigate among the operator surfaces without leaving the local console."""

        self.active_view = view

    def set_combiner(self, value: str) -> None:
        self.combiner = value

    def set_combiner_label(self, value: str) -> None:
        self.combiner = "all" if value == "Match ALL" else "any"

    def set_location_label(self, value: str) -> None:
        self.location = value.casefold().replace(" ", "_")

    def set_match_type_label(self, value: str) -> None:
        labels = {
            "Matches regex": "matches_regex",
            "Does not match regex": "not_matches_regex",
            "Contains": "contains",
            "Does not contain": "not_contains",
            "Equals": "equals",
            "Starts with": "starts_with",
            "Ends with": "ends_with",
            "Is empty": "is_empty",
        }
        self.match_type = labels.get(value, "matches_regex")

    def set_expression_type_label(self, value: str) -> None:
        self.expression_type = value.casefold()

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

    def add_expression(self) -> None:
        """Append a safe editable expression row to the draft."""

        self.additional_expressions = [
            *self.additional_expressions,
            {
                "type": "advanced",
                "location": "subject",
                "match_type": "contains",
                "value": "restricted",
                "description": "Additional operator expression",
            },
        ]
        self.preview_ready = False
        self.status = "Draft updated"

    def remove_expression(self, index: int | str) -> None:
        """Remove one additional expression by its visible row index."""

        row_index = int(index)
        if 0 <= row_index < len(self.additional_expressions):
            self.additional_expressions = [
                row
                for current_index, row in enumerate(self.additional_expressions)
                if current_index != row_index
            ]
            self.preview_ready = False
            self.status = "Draft updated"

    def update_expression(self, index: int | str, field: str, value: str) -> None:
        """Persist one editable value from an additional expression row."""

        row_index = int(index)
        if not 0 <= row_index < len(self.additional_expressions):
            return
        normalized_value = value
        if field == "type":
            normalized_value = value.casefold()
        elif field == "location":
            normalized_value = {
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
        elif field == "match_type":
            normalized_value = {
                "Matches regex": "matches_regex",
                "Does not match regex": "not_matches_regex",
                "Contains": "contains",
                "Does not contain": "not_contains",
                "Equals": "equals",
                "Starts with": "starts_with",
                "Ends with": "ends_with",
                "Is empty": "is_empty",
            }.get(value, "contains")

        updated_rows = [dict(row) for row in self.additional_expressions]
        updated_rows[row_index][field] = normalized_value
        self.additional_expressions = updated_rows
        self.preview_ready = False
        self.status = "Draft updated"

    def set_rule_name(self, value: str) -> None:
        self.rule_name = value

    def set_ou_path(self, value: str) -> None:
        self.ou_path = value

    def set_inbound(self, value: bool) -> None:
        self.inbound = value

    def set_outbound(self, value: bool) -> None:
        self.outbound = value

    def set_internal_sending(self, value: bool) -> None:
        self.internal_sending = value

    def set_internal_receiving(self, value: bool) -> None:
        self.internal_receiving = value

    def set_location(self, value: str) -> None:
        self.location = value

    def set_expression_type(self, value: str) -> None:
        self.expression_type = value

    def set_match_type(self, value: str) -> None:
        self.match_type = value

    def set_expression_value(self, value: str) -> None:
        self.expression_value = value

    def set_metadata_attribute(self, value: str) -> None:
        self.metadata_attribute = value

    def set_metadata_operator(self, value: str) -> None:
        self.metadata_operator = value

    def set_predefined_detector(self, value: str) -> None:
        self.predefined_detector = value

    def set_required_capability(self, value: str) -> None:
        self.required_capability = value

    def set_blocked_values(self, value: str) -> None:
        self.blocked_values = value

    def set_bypass_values(self, value: str) -> None:
        self.bypass_values = value

    def set_policy_category(self, value: str) -> None:
        self.policy_category = value

    def set_policy_id(self, value: str) -> None:
        self.policy_id = value

    def set_rejection_notice(self, value: str) -> None:
        self.rejection_notice = value

    def set_acknowledged(self, value: bool) -> None:
        self.acknowledged = value

    def set_phrase_entry(self, value: str) -> None:
        self.phrase_entry = value

    def select_section(self, section: str) -> None:
        """Switch between standard and advanced blocking editors."""

        self.section = section
        self.preview_ready = False
        self.approved = False
        self.status = "Draft"
        self.status_tone = "draft"

    def validate_expression(self) -> None:
        """Validate the current regex with the same RE2 engine used for execution."""

        try:
            if self.expression_type == "advanced" and self.match_type in {
                "matches_regex",
                "not_matches_regex",
            }:
                validate_google_regex(self.expression_value)
        except ValueError as error:
            self.expression_valid = False
            self.validation_message = str(error)
        else:
            self.expression_valid = True
            self.validation_message = "RE2 expression is valid"

    async def generate_persona(self) -> None:
        """Generate a fresh fictional persona and category-only bounce notice locally."""

        self.status = "Generating persona"
        self.status_tone = "working"
        self.error_message = ""
        try:
            generated = await build_persona_generator(load_settings()).generate(
                policy_category=self.policy_category,
                policy_id=self.policy_id,
            )
        except Exception as error:
            self.status = "Persona failed"
            self.status_tone = "error"
            self.error_message = str(error)
            return
        self._apply_generated_notice(generated)
        self.status = "Persona ready"
        self.status_tone = "ready"

    def preview(self) -> None:
        """Build the exact typed plan and bind a one-time approval phrase to its hash."""

        self.error_message = ""
        try:
            plan = self._build_plan()
        except (TypeError, ValueError) as error:
            self.preview_ready = False
            self.status = "Needs attention"
            self.status_tone = "error"
            self.error_message = str(error)
            return
        self.plan_hash = canonical_hash(plan)
        self.before_hash = "pending-live-read"
        self.change_hash = "pending-live-diff"
        self.live_evidence_bound = False
        self.approval_phrase = f"APPLY {self.plan_hash[:4].upper()}"
        self.phrase_entry = ""
        self.acknowledged = False
        self.preview_ready = True
        self.approved = False
        self.status = "Live read required"
        self.status_tone = "attention"

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

    def approve_plan(self) -> None:
        """Consume one exact approval; execution services must re-read before writing."""

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
        self.approved = True
        self.preview_ready = False
        self.status = "Approved for browser run"
        self.status_tone = "ready"
        self.error_message = ""

    def _apply_generated_notice(self, generated: GeneratedRejectionNotice) -> None:
        self.rejection_notice = generated.text
        self.persona_role = generated.persona.fictional_role
        self.persona_voice = generated.persona.voice
        self.persona_motif = generated.persona.motif
        self.persona_seed = generated.persona.seed
        self.persona_traits = list(generated.persona.traits)

    def _build_plan(self) -> TaskPlan:
        if self.section == "standard":
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
                        }
                    ],
                }
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
        draft = ContentComplianceRuleDraft(
            target_ou=OrganizationalUnitRef(path=self.ou_path),
            directions=directions,
            combiner=ExpressionCombiner(self.combiner),
            expressions=expressions,
            rejection_notice=notice,
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
        )


def _parse_address_entry(raw: str) -> AddressEntry | None:
    value = raw.strip()
    if not value:
        return None
    kind = "email" if "@" in value else "domain"
    return AddressEntry(kind=kind, value=value)


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
    )
