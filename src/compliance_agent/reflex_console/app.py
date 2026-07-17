"""Reference-faithful Reflex console for local Gmail policy administration."""

import reflex as rx

from compliance_agent.reflex_console.state import ConsoleState


def _icon(name: str, size: int = 18) -> rx.Component:
    return rx.icon(name, size=size, stroke_width=1.8)


def _nav_item(label: str, icon: str, view: str) -> rx.Component:
    return rx.button(
        _icon(icon),
        rx.text(label),
        on_click=ConsoleState.select_view(view),
        class_name=rx.cond(
            ConsoleState.active_view == view,
            "side-nav-item active",
            "side-nav-item",
        ),
    )


def _sidebar() -> rx.Component:
    return rx.box(
        rx.text("Gmail Policy Agent", class_name="sidebar-brand"),
        rx.vstack(
            _nav_item("Home", "house", "home"),
            rx.button(
                _icon("square-plus"),
                rx.text("New policy"),
                on_click=ConsoleState.start_new_policy,
                class_name=rx.cond(
                    ConsoleState.active_view == "new_policy",
                    "side-nav-item active",
                    "side-nav-item",
                ),
            ),
            _nav_item("Runs", "circle-play", "runs"),
            _nav_item("Ownership", "users", "ownership"),
            _nav_item("Audits", "shield", "audits"),
            _nav_item("Settings", "settings", "settings"),
            spacing="2",
            align="stretch",
            class_name="side-nav",
        ),
        rx.spacer(),
        rx.vstack(
            rx.hstack(rx.box(class_name="model-ready-dot"), rx.text("Local model configured")),
            rx.text(ConsoleState.model_label, class_name="sidebar-meta"),
            rx.hstack(_icon("lock", 14), rx.text("Credentials stay in Chrome")),
            rx.text("v1.0.0", class_name="sidebar-version"),
            spacing="3",
            align="start",
            class_name="sidebar-status",
        ),
        class_name="sidebar",
    )


def _top_status(icon: str, label: str, tone: str = "") -> rx.Component:
    return rx.hstack(
        _icon(icon, 17),
        rx.text(label),
        class_name=f"top-status {tone}".strip(),
    )


def _topbar() -> rx.Component:
    return rx.hstack(
        rx.hstack(
            _top_status("monitor", ConsoleState.model_label),
            _top_status(
                "triangle-alert",
                rx.cond(
                    ConsoleState.run_mode == "live",
                    "Exact approval required",
                    rx.cond(
                        ConsoleState.run_mode == "dry_run",
                        "Read-only Google preview",
                        "Planning only · no browser",
                    ),
                ),
                "warning",
            ),
            _top_status("lock", "Credentials stay in Chrome"),
            class_name="topbar-statuses",
        ),
        rx.spacer(),
        rx.hstack(
            rx.text("Mode", class_name="mode-label"),
            rx.select(
                ["Plan only", "Dry run", "Live"],
                value=ConsoleState.run_mode_label,
                on_change=ConsoleState.change_run_mode,
                aria_label="Execution mode",
                custom_attrs={"aria-label": "Execution mode"},
                disabled=ConsoleState.workflow_locked,
            ),
            rx.avatar(fallback="AD", size="2", class_name="admin-avatar"),
            class_name="mode-controls",
        ),
        class_name="topbar",
    )


def _policy_tabs() -> rx.Component:
    return rx.hstack(
        rx.button(
            "Blocked senders",
            on_click=ConsoleState.select_section("standard"),
            class_name=rx.cond(
                ConsoleState.section == "standard", "policy-tab active", "policy-tab"
            ),
        ),
        rx.button(
            "Content compliance",
            on_click=ConsoleState.select_section("compliance"),
            class_name=rx.cond(
                ConsoleState.section == "compliance", "policy-tab active", "policy-tab"
            ),
        ),
        class_name="policy-tabs",
    )


def _field_label(label: str) -> rx.Component:
    return rx.text(label, class_name="form-label")


def _direction(label: str, value: object, handler: object) -> rx.Component:
    return rx.box(
        rx.checkbox(label, checked=value, on_change=handler),
        class_name="direction-control",
    )


def _primary_expression_row() -> rx.Component:
    return rx.grid(
        rx.text("1", class_name="row-number"),
        rx.select(
            ["Advanced", "Simple", "Metadata", "Predefined"],
            value=ConsoleState.expression_type_label,
            on_change=ConsoleState.set_expression_type_label,
            aria_label="Expression type",
            custom_attrs={"aria-label": "Expression type"},
        ),
        rx.select(
            [
                "Full headers",
                "Headers and body",
                "Body",
                "Subject",
                "Sender header",
                "Recipient header",
                "Envelope sender",
                "Envelope recipient",
                "Raw message",
            ],
            value=ConsoleState.location_label,
            on_change=ConsoleState.set_location_label,
            disabled=ConsoleState.expression_type != "advanced",
            aria_label="Expression location",
            custom_attrs={"aria-label": "Expression location"},
        ),
        rx.select(
            [
                "Matches regex",
                "Does not match regex",
                "Contains",
                "Does not contain",
                "Equals",
                "Starts with",
                "Ends with",
                "Is empty",
                "Matches any word",
                "Matches all words",
            ],
            value=ConsoleState.match_type_label,
            on_change=ConsoleState.set_match_type_label,
            disabled=ConsoleState.expression_type != "advanced",
            aria_label="Expression match type",
            custom_attrs={"aria-label": "Expression match type"},
        ),
        rx.input(
            value=ConsoleState.expression_value,
            on_change=ConsoleState.set_expression_value,
            on_blur=ConsoleState.validate_expression,
            aria_label="Expression 1 value",
            class_name="expression-input",
            disabled=(ConsoleState.expression_type == "predefined")
            | (
                (ConsoleState.expression_type == "advanced")
                & (ConsoleState.match_type == "is_empty")
            ),
        ),
        rx.button(_icon("trash-2", 16), class_name="icon-button muted", disabled=True),
        columns=(
            "36px minmax(100px, 128px) minmax(110px, 152px) "
            "minmax(130px, 168px) minmax(160px, 1fr) 42px"
        ),
        gap="10px",
        align_items="center",
        class_name="expression-row",
    )


def _additional_expression_details(row: object, index: object) -> rx.Component:
    return rx.cond(
        (row["type"] == "advanced")
        & (
            (row["match_type"] == "matches_regex")
            | (row["match_type"] == "not_matches_regex")
        ),
        rx.grid(
            rx.input(
                value=row["description"],
                on_change=ConsoleState.update_expression(index, "description"),
                placeholder="Regex description",
                aria_label="Regex description",
            ),
            rx.input(
                type="number",
                min="1",
                value=row["minimum_match_count"],
                on_change=ConsoleState.update_expression(index, "minimum_match_count"),
                aria_label="Minimum matches",
            ),
            columns="2",
            class_name="expression-details",
        ),
        rx.cond(
            row["type"] == "metadata",
            rx.grid(
                rx.select(
                    [
                        "message_authentication",
                        "source_ip",
                        "secure_transport",
                        "smime_encryption",
                        "smime_signature",
                        "message_size",
                        "gmail_confidential_mode",
                        "security_sandbox_malware",
                    ],
                    value=row["attribute"],
                    on_change=ConsoleState.update_expression(index, "attribute"),
                    aria_label="Metadata attribute",
                ),
                rx.select(
                    [
                        "authenticated",
                        "not_authenticated",
                        "within_range",
                        "not_within_range",
                        "tls",
                        "not_tls",
                        "encrypted",
                        "not_encrypted",
                        "signed",
                        "not_signed",
                        "greater_than_mb",
                        "less_than_mb",
                        "confidential",
                        "not_confidential",
                        "malware_detected",
                    ],
                    value=row["operator"],
                    on_change=ConsoleState.update_expression(index, "operator"),
                    aria_label="Metadata operator",
                ),
                columns="2",
                class_name="expression-details",
            ),
            rx.cond(
                row["type"] == "predefined",
                rx.grid(
                    rx.input(
                        value=row["detector"],
                        on_change=ConsoleState.update_expression(index, "detector"),
                        placeholder="Predefined detector",
                        aria_label="Predefined detector",
                    ),
                    rx.input(
                        value=row["required_capability"],
                        on_change=ConsoleState.update_expression(index, "required_capability"),
                        placeholder="Edition capability",
                        aria_label="Required edition capability",
                    ),
                    rx.input(
                        type="number",
                        min="1",
                        value=row["minimum_match_count"],
                        on_change=ConsoleState.update_expression(
                            index, "minimum_match_count"
                        ),
                        aria_label="Minimum matches",
                    ),
                    rx.select(
                        ["none", "low", "medium", "high"],
                        value=row["confidence"],
                        on_change=ConsoleState.update_expression(index, "confidence"),
                        aria_label="Predefined confidence",
                    ),
                    columns="4",
                    class_name="expression-details",
                ),
                rx.box(),
            ),
        ),
    )


def _additional_expression_row(row: object, index: object) -> rx.Component:
    advanced = row["type"] == "advanced"
    return rx.vstack(
        rx.grid(
            rx.text(index + 2, class_name="row-number"),
            rx.select(
                ["Advanced", "Simple", "Metadata", "Predefined"],
                value=row["type_label"],
                on_change=ConsoleState.update_expression(index, "type_label"),
                aria_label="Expression type",
                custom_attrs={"aria-label": "Expression type"},
            ),
            rx.select(
                [
                    "Full headers",
                    "Headers and body",
                    "Body",
                    "Subject",
                    "Sender header",
                    "Recipient header",
                    "Envelope sender",
                    "Envelope recipient",
                    "Raw message",
                ],
                value=row["location_label"],
                on_change=ConsoleState.update_expression(index, "location_label"),
                disabled=~advanced,
                aria_label="Expression location",
                custom_attrs={"aria-label": "Expression location"},
            ),
            rx.select(
                [
                    "Matches regex",
                    "Does not match regex",
                    "Contains",
                    "Does not contain",
                    "Equals",
                    "Starts with",
                    "Ends with",
                    "Is empty",
                    "Matches any word",
                    "Matches all words",
                ],
                value=row["match_type_label"],
                on_change=ConsoleState.update_expression(index, "match_type_label"),
                disabled=~advanced,
                aria_label="Expression match type",
                custom_attrs={"aria-label": "Expression match type"},
            ),
            rx.input(
                value=row["value"],
                on_change=ConsoleState.update_expression(index, "value"),
                aria_label="Additional expression value",
                disabled=(row["type"] == "predefined")
                | ((row["type"] == "advanced") & (row["match_type"] == "is_empty")),
            ),
            rx.button(
                _icon("trash-2", 16),
                on_click=ConsoleState.remove_expression(index),
                class_name="icon-button",
                aria_label="Remove expression",
            ),
            columns=(
                "36px minmax(100px, 128px) minmax(110px, 152px) "
                "minmax(130px, 168px) minmax(160px, 1fr) 42px"
            ),
            gap="10px",
            align_items="center",
            class_name="expression-row",
        ),
        _additional_expression_details(row, index),
        spacing="1",
        align="stretch",
        width="100%",
    )


def _expression_details() -> rx.Component:
    return rx.cond(
        ConsoleState.expression_type == "metadata",
        rx.grid(
            rx.vstack(
                _field_label("Metadata attribute"),
                rx.select(
                    [
                        "Message authentication",
                        "Source IP",
                        "Secure transport (TLS)",
                        "S/MIME encryption",
                        "S/MIME signature",
                        "Message size",
                        "Gmail confidential mode",
                        "Security sandbox malware",
                    ],
                    value=ConsoleState.metadata_attribute_label,
                    on_change=ConsoleState.set_metadata_attribute_label,
                    custom_attrs={"aria-label": "Metadata attribute"},
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Metadata operator"),
                rx.select(
                    ConsoleState.metadata_operator_options,
                    value=ConsoleState.metadata_operator_label,
                    on_change=ConsoleState.set_metadata_operator_label,
                    custom_attrs={"aria-label": "Metadata operator"},
                ),
                align="stretch",
                spacing="1",
            ),
            columns="2",
            gap="16px",
            class_name="expression-details",
        ),
        rx.cond(
            ConsoleState.expression_type == "predefined",
            rx.grid(
                rx.vstack(
                    _field_label("Predefined detector"),
                    rx.input(
                        value=ConsoleState.predefined_detector,
                        on_change=ConsoleState.set_predefined_detector,
                    ),
                    align="stretch",
                    spacing="1",
                ),
                rx.vstack(
                    _field_label("Required edition capability"),
                    rx.input(
                        value=ConsoleState.required_capability,
                        on_change=ConsoleState.set_required_capability,
                    ),
                    align="stretch",
                    spacing="1",
                ),
                rx.vstack(
                    _field_label("Minimum matches"),
                    rx.input(
                        type="number",
                        min="1",
                        value=ConsoleState.minimum_match_count,
                        on_change=ConsoleState.set_minimum_match_count,
                    ),
                    align="stretch",
                    spacing="1",
                ),
                rx.vstack(
                    _field_label("Confidence"),
                    rx.select(
                        ["none", "low", "medium", "high"],
                        value=ConsoleState.predefined_confidence,
                        on_change=ConsoleState.set_predefined_confidence,
                    ),
                    align="stretch",
                    spacing="1",
                ),
                columns="4",
                gap="16px",
                class_name="expression-details",
            ),
            rx.cond(
                (ConsoleState.expression_type == "advanced")
                & (
                    (ConsoleState.match_type == "matches_regex")
                    | (ConsoleState.match_type == "not_matches_regex")
                ),
                rx.grid(
                    rx.vstack(
                        _field_label("Regex description"),
                        rx.input(
                            value=ConsoleState.regex_description,
                            on_change=ConsoleState.set_regex_description,
                        ),
                        align="stretch",
                        spacing="1",
                    ),
                    rx.vstack(
                        _field_label("Minimum matches"),
                        rx.input(
                            type="number",
                            min="1",
                            value=ConsoleState.minimum_match_count,
                            on_change=ConsoleState.set_minimum_match_count,
                        ),
                        align="stretch",
                        spacing="1",
                    ),
                    columns="2",
                    gap="16px",
                    class_name="expression-details",
                ),
            ),
        ),
    )


def _compliance_scope_filters() -> rx.Component:
    selectors = ["Single address", "Pattern (RE2)", "Group membership"]
    return rx.box(
        rx.heading("Additional compliance scope", size="4"),
        rx.grid(
            rx.vstack(
                _field_label("Address-list behavior"),
                rx.select(
                    [
                        "No address-list condition",
                        "Bypass listed addresses",
                        "Only apply to listed addresses",
                    ],
                    value=ConsoleState.compliance_address_list_mode_label,
                    on_change=ConsoleState.set_compliance_address_list_mode_label,
                    custom_attrs={"aria-label": "Address-list behavior"},
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Address-list names (one per line)"),
                rx.text_area(
                    value=ConsoleState.compliance_address_lists,
                    on_change=ConsoleState.set_compliance_address_lists,
                    placeholder="Trusted partners",
                    disabled=ConsoleState.compliance_address_list_mode == "none",
                ),
                align="stretch",
                spacing="1",
            ),
            columns="2",
            gap="16px",
        ),
        rx.grid(
            rx.vstack(
                rx.checkbox(
                    "Filter envelope sender",
                    checked=ConsoleState.sender_filter_enabled,
                    on_change=ConsoleState.set_sender_filter_enabled,
                ),
                rx.select(
                    selectors,
                    value=ConsoleState.sender_filter_selector_label,
                    on_change=ConsoleState.set_sender_filter_selector_label,
                    disabled=~ConsoleState.sender_filter_enabled,
                    custom_attrs={"aria-label": "Envelope sender filter type"},
                ),
                rx.input(
                    value=ConsoleState.sender_filter_value,
                    on_change=ConsoleState.set_sender_filter_value,
                    placeholder="sender@example.com or RE2 pattern",
                    disabled=~ConsoleState.sender_filter_enabled,
                ),
                align="stretch",
                spacing="2",
            ),
            rx.vstack(
                rx.checkbox(
                    "Filter envelope recipient",
                    checked=ConsoleState.recipient_filter_enabled,
                    on_change=ConsoleState.set_recipient_filter_enabled,
                ),
                rx.select(
                    selectors,
                    value=ConsoleState.recipient_filter_selector_label,
                    on_change=ConsoleState.set_recipient_filter_selector_label,
                    disabled=~ConsoleState.recipient_filter_enabled,
                    custom_attrs={"aria-label": "Envelope recipient filter type"},
                ),
                rx.input(
                    value=ConsoleState.recipient_filter_value,
                    on_change=ConsoleState.set_recipient_filter_value,
                    placeholder="recipient@example.com or RE2 pattern",
                    disabled=~ConsoleState.recipient_filter_enabled,
                ),
                align="stretch",
                spacing="2",
            ),
            columns="2",
            gap="16px",
            margin_top="16px",
        ),
        class_name="form-section",
    )


def _compliance_editor() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            _field_label("Organizational unit"),
            rx.input(
                value=ConsoleState.ou_path,
                on_change=ConsoleState.set_ou_path,
                placeholder="/ or /Finance/Accounts Payable",
                aria_label="Organizational unit path",
            ),
            rx.text(
                "Use the exact absolute Google Admin OU path. Inherited rules remain read-only.",
                class_name="field-help",
            ),
            align="stretch",
            spacing="1",
            class_name="ou-field",
        ),
        rx.vstack(
            _field_label("Email messages to affect"),
            rx.grid(
                _direction("Inbound", ConsoleState.inbound, ConsoleState.set_inbound),
                _direction("Outbound", ConsoleState.outbound, ConsoleState.set_outbound),
                _direction(
                    "Internal-Sending",
                    ConsoleState.internal_sending,
                    ConsoleState.set_internal_sending,
                ),
                _direction(
                    "Internal-Receiving",
                    ConsoleState.internal_receiving,
                    ConsoleState.set_internal_receiving,
                ),
                columns="4",
                gap="8px",
                width="100%",
            ),
            align="stretch",
            spacing="1",
            width="100%",
        ),
        rx.vstack(
            _field_label("Expression combiner"),
            rx.select(
                ["Match ANY expression", "Match ALL expressions"],
                value=ConsoleState.combiner_label,
                on_change=ConsoleState.set_combiner_label,
                custom_attrs={"aria-label": "Expression combiner"},
            ),
            align="stretch",
            spacing="1",
            class_name="combiner-field",
        ),
        rx.grid(
            rx.text("#"),
            rx.text("Type"),
            rx.text("Location"),
            rx.text("Match"),
            rx.text("Expression"),
            rx.text(""),
            columns=(
                "36px minmax(100px, 128px) minmax(110px, 152px) "
                "minmax(130px, 168px) minmax(160px, 1fr) 42px"
            ),
            gap="10px",
            class_name="expression-labels",
        ),
        _primary_expression_row(),
        rx.foreach(ConsoleState.additional_expressions, _additional_expression_row),
        _expression_details(),
        _compliance_scope_filters(),
        rx.hstack(
            rx.button(
                _icon("plus", 16),
                "Add expression",
                on_click=ConsoleState.add_expression,
                class_name="text-button",
            ),
            rx.spacer(),
            rx.hstack(
                rx.box(
                    class_name=rx.cond(
                        ConsoleState.expression_valid,
                        "validation-dot good",
                        "validation-dot bad",
                    )
                ),
                rx.text(ConsoleState.validation_message),
                class_name="validation-status",
            ),
            width="100%",
        ),
        spacing="4",
        align="stretch",
        width="100%",
        class_name="policy-form",
    )


def _standard_editor() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            _field_label("Organizational unit"),
            rx.input(
                value=ConsoleState.ou_path,
                on_change=ConsoleState.set_ou_path,
                placeholder="/ or /Finance/Accounts Payable",
                aria_label="Organizational unit path",
                disabled=ConsoleState.standard_ou_locked,
            ),
            rx.text(
                rx.cond(
                    ConsoleState.standard_ou_locked,
                    "The organizational unit is immutable for an owned blocked-sender policy; "
                    "create a new policy to use another OU.",
                    "The visible rule and address-list names use an immutable managed ID.",
                ),
                class_name="field-help",
            ),
            align="stretch",
            spacing="1",
            class_name="ou-field",
        ),
        rx.grid(
            rx.vstack(
                _field_label("Domains or email addresses"),
                rx.text_area(
                    value=ConsoleState.blocked_values,
                    on_change=ConsoleState.set_blocked_values,
                    rows="6",
                    placeholder="example.com or sender@example.com, one per line",
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Approved-sender bypasses"),
                rx.text_area(
                    value=ConsoleState.bypass_values,
                    on_change=ConsoleState.set_bypass_values,
                    rows="6",
                    placeholder="trusted.example or sender@trusted.example",
                ),
                align="stretch",
                spacing="1",
            ),
            columns="2",
            gap="16px",
            width="100%",
        ),
        spacing="4",
        align="stretch",
        width="100%",
        class_name="policy-form standard-form",
    )


def _rejection_editor() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.hstack(
                rx.heading("Rejection notice", size="4", class_name="section-heading"),
                _icon("info", 15),
            ),
            rx.spacer(),
            rx.text("Policy ID " + ConsoleState.policy_id, class_name="policy-id"),
            width="100%",
        ),
        rx.grid(
            rx.vstack(
                _field_label("Policy ID included in the notice"),
                rx.input(
                    value=ConsoleState.policy_id,
                    on_change=ConsoleState.set_policy_id,
                    placeholder="GW-1042",
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Bounce-message category"),
                rx.input(
                    value=ConsoleState.policy_category,
                    on_change=ConsoleState.set_policy_category,
                    placeholder="confidential-information",
                ),
                align="stretch",
                spacing="1",
            ),
            columns="2",
            gap="16px",
            margin_top="12px",
        ),
        rx.box(
            rx.hstack(
                rx.hstack(
                    _icon("text", 15),
                    rx.text("Plain-text SMTP rejection notice"),
                    class_name="plain-text-label",
                ),
                rx.spacer(),
                rx.button(
                    _icon("sparkles", 15),
                    rx.cond(
                        ConsoleState.persona_in_progress,
                        "Generating persona…",
                        "Regenerate persona",
                    ),
                    on_click=ConsoleState.generate_persona,
                    disabled=ConsoleState.persona_in_progress,
                    class_name="persona-action",
                ),
                width="100%",
                class_name="editor-toolbar",
            ),
            rx.text_area(
                value=ConsoleState.rejection_notice,
                on_change=ConsoleState.set_rejection_notice,
                rows="8",
                class_name="notice-textarea",
                aria_label="Rejection notice text",
            ),
            rx.hstack(
                rx.hstack(
                    rx.box("✦", class_name="persona-mark"),
                    rx.text(ConsoleState.persona_role, class_name="persona-role"),
                    rx.text("· " + ConsoleState.persona_voice, class_name="persona-voice"),
                ),
                rx.spacer(),
                rx.text(ConsoleState.notice_character_count, " characters"),
                class_name="editor-footer",
            ),
            class_name="notice-editor",
        ),
        class_name="form-section rejection-section",
    )


def _impact_row(
    scope: str,
    before: object,
    after: object,
    change: str,
    tone: str = "",
) -> rx.Component:
    return rx.grid(
        rx.text(scope, class_name="impact-scope"),
        rx.text(before),
        rx.text(after),
        rx.text(change, class_name=f"impact-change {tone}".strip()),
        columns="190px 1fr 1fr 130px",
        class_name="impact-row",
    )


def _impact_summary() -> rx.Component:
    return rx.box(
        rx.text("Impact summary (before → after)", class_name="impact-title"),
        rx.box(
            rx.grid(
                rx.text("Scope"),
                rx.text("Before (current policy)"),
                rx.text("After (proposed policy)"),
                rx.text("Change"),
                columns="190px 1fr 1fr 130px",
                class_name="impact-header",
            ),
            _impact_row(
                "Current Google state",
                ConsoleState.before_summary,
                ConsoleState.after_summary,
                ConsoleState.change_summary,
                "good",
            ),
            rx.cond(
                ConsoleState.section == "compliance",
                rx.box(
                    _impact_row(
                        "Email directions",
                        "Current policy",
                        ConsoleState.direction_summary,
                        "Scoped",
                    ),
                    _impact_row(
                        "Expressions",
                        "Current policy",
                        ConsoleState.expression_count,
                        "Typed",
                    ),
                ),
                rx.box(
                    _impact_row(
                        "Blocked identities",
                        "Current policy",
                        ConsoleState.blocked_entry_count,
                        "Typed",
                    ),
                    _impact_row(
                        "Approved bypasses",
                        "Current policy",
                        ConsoleState.bypass_entry_count,
                        "Typed",
                    ),
                ),
            ),
            _impact_row(
                "Rejection notice",
                "Default rejection",
                "Custom notice (" + ConsoleState.policy_id + ")",
                "Changed",
                "good",
            ),
            class_name="impact-table",
        ),
        rx.text(
            "Impact estimates stay pending until a fresh Google Admin read provides "
            "tenant evidence.",
            class_name="impact-note",
        ),
        class_name="form-section impact-section",
    )


def _draft_evidence() -> rx.Component:
    return rx.cond(
        ConsoleState.preview_ready,
        rx.box(
            rx.hstack(
                rx.hstack(
                    _icon("file-check-2", 17),
                    rx.text(
                        rx.cond(
                            ConsoleState.live_evidence_bound,
                            "Live browser evidence ready",
                            "Review evidence ready",
                        )
                    ),
                ),
                rx.spacer(),
                rx.text(ConsoleState.status, class_name="evidence-status"),
                width="100%",
            ),
            rx.grid(
                rx.text("Plan"),
                rx.text(ConsoleState.plan_hash, class_name="evidence-hash"),
                rx.text("Before"),
                rx.text(ConsoleState.before_hash, class_name="evidence-hash"),
                rx.text("Change"),
                rx.text(ConsoleState.change_hash, class_name="evidence-hash"),
                columns="72px minmax(0, 1fr)",
                class_name="evidence-grid",
            ),
            rx.text(
                rx.cond(
                    ConsoleState.live_evidence_bound,
                    "These exact hashes are bound to the one-time approval below.",
                    rx.cond(
                        ConsoleState.status == "No change",
                        "Current and expected state match; no approval or write is required.",
                        rx.cond(
                            ConsoleState.run_mode == "dry_run",
                            "This browser-backed preview is read-only; no approval exists.",
                            "This is a planning artifact; Google Admin was not opened.",
                        ),
                    ),
                ),
                class_name="evidence-copy",
            ),
            class_name="draft-evidence",
        ),
    )


def _policy_editor() -> rx.Component:
    focused_operation = (ConsoleState.operation == "remove") | (
        ConsoleState.operation == "toggle"
    )
    return rx.box(
        rx.heading(
            rx.cond(
                ConsoleState.operation == "create",
                "Create Gmail policy",
                rx.cond(
                    ConsoleState.operation == "remove",
                    "Remove managed Gmail policy",
                    rx.cond(
                        ConsoleState.operation == "toggle",
                        "Change managed policy state",
                        "Edit managed Gmail policy",
                    ),
                ),
            ),
            size="7",
            class_name="page-title",
        ),
        rx.el.fieldset(
            rx.cond(
                focused_operation,
                _focused_operation_summary(),
                rx.box(
                    rx.cond(
                        ConsoleState.operation == "create",
                        _policy_tabs(),
                        rx.hstack(
                            _icon("lock", 14),
                            rx.text(
                                rx.cond(
                                    ConsoleState.section == "compliance",
                                    "Editing owned Content compliance policy",
                                    "Editing owned blocked-sender policy",
                                )
                            ),
                            class_name="surface-lock",
                        ),
                    ),
                    rx.hstack(
                        rx.checkbox(
                            "Policy enabled",
                            checked=ConsoleState.rule_enabled,
                            on_change=ConsoleState.set_rule_enabled,
                        ),
                        width="100%",
                        class_name="form-section",
                    ),
                    rx.cond(
                        ConsoleState.section == "compliance",
                        _compliance_editor(),
                        _standard_editor(),
                    ),
                    _rejection_editor(),
                ),
            ),
            disabled=ConsoleState.workflow_locked,
            class_name="editor-fieldset",
        ),
        _impact_summary(),
        _draft_evidence(),
        rx.hstack(
            rx.cond(
                ~ConsoleState.draft_minimum_ready,
                rx.text(
                    ConsoleState.draft_readiness_message,
                    class_name="required-hint",
                ),
            ),
            rx.spacer(),
            rx.button(
                rx.cond(
                    ConsoleState.browser_in_progress,
                    "Reading Google Admin…",
                    rx.cond(
                        ConsoleState.review_in_progress,
                        "Reviewing with agents…",
                        rx.cond(
                            ConsoleState.run_mode == "plan_only",
                            "Review plan",
                            "Review and preview",
                        ),
                    ),
                ),
                _icon("arrow-right", 17),
                on_click=ConsoleState.preview,
                disabled=(~ConsoleState.draft_minimum_ready)
                | ConsoleState.review_in_progress
                | ConsoleState.browser_in_progress,
                class_name="primary-action",
            ),
            width="100%",
            class_name="review-row",
        ),
        class_name="policy-canvas",
    )


def _agent_timeline_item(agent: object) -> rx.Component:
    return rx.hstack(
        rx.box(_icon(agent["icon"], 19), class_name="agent-icon"),
        rx.vstack(
            rx.hstack(
                rx.text(agent["name"], class_name="agent-title"),
                rx.spacer(),
                rx.text(agent["time"], class_name="agent-time"),
                width="100%",
            ),
            rx.text(agent["status"], class_name="agent-message"),
            align="stretch",
            spacing="2",
            width="100%",
        ),
        align="start",
        class_name="timeline-item",
        width="100%",
    )


def _agent_rail() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text("Agent group chat", class_name="agent-rail-title"),
            rx.hstack(_icon("users", 14), rx.text("4"), class_name="agent-count"),
            rx.spacer(),
            width="100%",
            class_name="agent-rail-header",
        ),
        rx.box(
            rx.foreach(ConsoleState.agent_activity, _agent_timeline_item),
            class_name="agent-timeline",
        ),
        rx.spacer(),
        rx.box(
            rx.text(
                ConsoleState.approval_state_label,
                class_name="approval-state",
            ),
            rx.cond(
                ConsoleState.live_evidence_bound,
                rx.vstack(
                    rx.checkbox(
                        "I reviewed the exact before/after evidence",
                        checked=ConsoleState.acknowledged,
                        on_change=ConsoleState.set_acknowledged,
                        disabled=ConsoleState.execution_in_progress,
                    ),
                    rx.input(
                        value=ConsoleState.phrase_entry,
                        on_change=ConsoleState.set_phrase_entry,
                        placeholder=ConsoleState.approval_phrase,
                        aria_label="Exact approval phrase",
                        disabled=ConsoleState.execution_in_progress,
                    ),
                    rx.text(
                        "Type " + ConsoleState.approval_phrase,
                        class_name="approval-phrase-help",
                    ),
                    align="stretch",
                    spacing="2",
                    width="100%",
                ),
            ),
            rx.hstack(
                rx.hstack(
                    rx.avatar(fallback="AD", size="2"),
                    rx.text("Workspace Admin (you)"),
                ),
                rx.spacer(),
                rx.button(
                    rx.cond(
                        ConsoleState.execution_in_progress,
                        "Applying…",
                        rx.cond(ConsoleState.live_evidence_bound, "Approve & apply", "Locked"),
                    ),
                    on_click=ConsoleState.approve_plan,
                    disabled=(~ConsoleState.live_evidence_bound)
                    | ConsoleState.execution_in_progress,
                    class_name="approve-button",
                ),
                width="100%",
            ),
            rx.cond(
                ConsoleState.error_message != "",
                rx.text(ConsoleState.error_message, class_name="approval-error"),
            ),
            class_name="approval-footer",
        ),
        class_name="agent-rail",
    )


def _secondary_table_row(label: str, detail: str, status: str) -> rx.Component:
    return rx.grid(
        rx.text(label, class_name="secondary-row-title"),
        rx.text(detail),
        rx.text(status, class_name="secondary-status"),
        columns="220px 1fr 150px",
        class_name="secondary-row",
    )


def _secondary_view(
    title: str,
    subtitle: str,
    rows: tuple[tuple[str, str, str], ...],
) -> rx.Component:
    return rx.box(
        rx.heading(title, size="7", class_name="page-title"),
        rx.text(subtitle, class_name="secondary-subtitle"),
        rx.box(
            *(_secondary_table_row(*row) for row in rows),
            class_name="secondary-table",
        ),
        class_name="secondary-view",
    )


def _focused_operation_summary() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.box(
                rx.cond(
                    ConsoleState.operation == "remove",
                    _icon("trash-2", 20),
                    _icon("power", 20),
                ),
                class_name=rx.cond(
                    ConsoleState.operation == "remove",
                    "operation-icon danger",
                    "operation-icon",
                ),
            ),
            rx.vstack(
                rx.heading(
                    rx.cond(
                        ConsoleState.operation == "remove",
                        "Confirm managed policy removal",
                        rx.cond(
                            ConsoleState.rule_enabled,
                            "Confirm policy enablement",
                            "Confirm policy disablement",
                        ),
                    ),
                    size="4",
                ),
                rx.text(ConsoleState.rule_name, class_name="secondary-row-title"),
                rx.text(
                    rx.cond(
                        ConsoleState.section == "compliance",
                        "Content compliance",
                        "Blocked senders",
                    ),
                    " · ",
                    ConsoleState.ou_path,
                    class_name="secondary-subtitle operation-subtitle",
                ),
                align="start",
                spacing="1",
            ),
            align="start",
            class_name="operation-heading",
        ),
        rx.text(
            rx.cond(
                ConsoleState.operation == "remove",
                "Only the exact locally owned policy is removed. Linked address lists are "
                "removed only when ownership evidence proves they belong to this policy.",
                "Only the enabled state changes; every expression, address, scope, and "
                "rejection notice remains unchanged.",
            ),
            class_name="operation-copy",
        ),
        rx.text(
            "A fresh Google read and exact live approval are still required.",
            class_name="field-help",
        ),
        class_name="form-section focused-operation",
    )


def _settings_view() -> rx.Component:
    return rx.box(
        rx.heading("Settings", size="7", class_name="page-title"),
        rx.text(
            "Execution mode changes apply immediately to the next review. Google credentials "
            "are entered only in the headed Chrome window.",
            class_name="secondary-subtitle",
        ),
        rx.el.fieldset(
            _field_label("Run mode"),
            rx.select(
                ["Plan only", "Dry run", "Live"],
                value=ConsoleState.run_mode_label,
                on_change=ConsoleState.change_run_mode,
                width="100%",
                custom_attrs={"aria-label": "Settings execution mode"},
                disabled=ConsoleState.workflow_locked,
            ),
            _field_label("Expected administrator email"),
            rx.input(
                value=ConsoleState.expected_admin_email,
                on_change=ConsoleState.set_expected_admin_email,
                placeholder="admin@example.com",
                width="100%",
            ),
            _field_label("Workspace domain"),
            rx.input(
                value=ConsoleState.workspace_domain,
                on_change=ConsoleState.set_workspace_domain,
                placeholder="example.com",
                width="100%",
            ),
            rx.button(
                "Save Google identities",
                on_click=ConsoleState.save_google_identities,
                class_name="primary-action",
            ),
            rx.box(class_name="settings-divider"),
            rx.heading("Local agent models", size="4"),
            rx.text(
                "The group chat uses the orchestration model. Google Admin navigation uses a "
                "separate vision-capable browser model.",
                class_name="field-help",
            ),
            _field_label("Group-chat and persona model"),
            rx.input(
                value=ConsoleState.orchestration_model,
                on_change=ConsoleState.set_orchestration_model,
                placeholder="gemma4:12b",
                width="100%",
            ),
            _field_label("Browser vision model"),
            rx.input(
                value=ConsoleState.browser_model,
                on_change=ConsoleState.set_browser_model,
                placeholder="gemma4:12b",
                width="100%",
            ),
            rx.button(
                "Save local models",
                on_click=ConsoleState.save_agent_models,
                class_name="secondary-action",
            ),
            rx.cond(
                ConsoleState.configuration_message != "",
                rx.text(
                    ConsoleState.configuration_message,
                    class_name=rx.cond(
                        ConsoleState.configuration_tone == "error",
                        "configuration-message error",
                        "configuration-message success",
                    ),
                ),
            ),
            disabled=ConsoleState.workflow_locked,
            class_name="form-section editor-fieldset",
        ),
        class_name="secondary-view",
    )


def _managed_policy_item(policy: object) -> rx.Component:
    return rx.grid(
        rx.vstack(
            rx.text(policy["name"], class_name="secondary-row-title"),
            rx.text(policy["surface_label"] + " · " + policy["ou"]),
            spacing="1",
        ),
        rx.text(policy["enabled"], class_name="secondary-status"),
        rx.hstack(
            rx.button(
                "Edit",
                on_click=ConsoleState.edit_policy(policy["surface"], policy["id"]),
                class_name="table-action",
            ),
            rx.button(
                rx.cond(policy["enabled"] == "Enabled", "Disable", "Enable"),
                on_click=ConsoleState.toggle_policy(policy["surface"], policy["id"]),
                class_name="table-action",
            ),
            rx.button(
                "Remove",
                on_click=ConsoleState.remove_policy(policy["surface"], policy["id"]),
                class_name="table-action danger",
            ),
        ),
        columns="minmax(260px, 1fr) 100px 250px",
        class_name="secondary-row",
    )


def _ownership_view() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.vstack(
                rx.heading("Managed ownership", size="7", class_name="page-title"),
                rx.text(
                    "Edit, enable, disable, or remove only policies with matching local and "
                    "visible Google ownership evidence.",
                    class_name="secondary-subtitle",
                ),
                align="start",
            ),
            rx.spacer(),
            rx.button(
                "New blocked-sender policy",
                on_click=ConsoleState.start_create("standard"),
                class_name="primary-action",
            ),
            rx.button(
                "New compliance policy",
                on_click=ConsoleState.start_create("compliance"),
                class_name="primary-action",
            ),
            width="100%",
            align="end",
            class_name="ownership-header",
        ),
        rx.cond(
            ConsoleState.managed_policies.length() > 0,
            rx.box(
                rx.foreach(ConsoleState.managed_policies, _managed_policy_item),
                class_name="secondary-table",
            ),
            rx.text("No verified managed policies yet."),
        ),
        class_name="secondary-view",
    )


def _run_history_item(run: object) -> rx.Component:
    return rx.grid(
        rx.text(run["run_id"], class_name="secondary-row-title"),
        rx.vstack(
            rx.text(run["surface"] + " · " + run["mode"]),
            rx.text(run["time"], class_name="field-help"),
            spacing="1",
        ),
        rx.vstack(rx.text(run["status"]), rx.text(run["detail"]), spacing="1"),
        columns="120px 1fr 1.5fr",
        class_name="secondary-row",
    )


def _runs_view() -> rx.Component:
    return rx.box(
        rx.heading("Runs", size="7", class_name="page-title"),
        rx.text(
            "Planning, preview, drift, execution, and verification states from this session.",
            class_name="secondary-subtitle",
        ),
        rx.cond(
            ConsoleState.run_history.length() > 0,
            rx.box(
                rx.foreach(ConsoleState.run_history, _run_history_item),
                class_name="secondary-table",
            ),
            rx.text("No runs yet. Create a policy to begin."),
        ),
        class_name="secondary-view",
    )


def _audit_history_item(run: object) -> rx.Component:
    return rx.grid(
        rx.text(run["run_id"], class_name="secondary-row-title audit-run-id"),
        rx.text(run["started"]),
        rx.text(run["status"]),
        rx.text(
            run["integrity"],
            class_name=rx.cond(
                run["integrity"] == "Integrity verified",
                "secondary-status",
                "integrity-warning",
            ),
        ),
        rx.hstack(
            rx.button(
                "Open",
                on_click=ConsoleState.open_audit_folder(run["full_id"]),
                disabled=run["full_id"] == "",
                class_name="table-action",
            ),
            rx.button(
                "Export ZIP",
                on_click=ConsoleState.export_audit_package(run["full_id"]),
                disabled=run["full_id"] == "",
                class_name="table-action",
            ),
            spacing="2",
        ),
        columns=(
            "120px minmax(170px, 1fr) minmax(150px, 1fr) 170px minmax(170px, auto)"
        ),
        class_name="secondary-row audit-row",
    )


def _audits_view() -> rx.Component:
    return rx.box(
        rx.heading("Audit evidence", size="7", class_name="page-title"),
        rx.text(
            "Terminal run manifests are verified against artifact digests and the hash-chained "
            "event stream every time this page loads.",
            class_name="secondary-subtitle",
        ),
        rx.cond(
            ConsoleState.audit_history.length() > 0,
            rx.box(
                rx.foreach(ConsoleState.audit_history, _audit_history_item),
                class_name="secondary-table",
            ),
            rx.box(
                rx.text("No terminal audit packages yet.", class_name="empty-title"),
                rx.text(
                    "Dry-run previews and completed or rejected live approvals appear here.",
                    class_name="field-help",
                ),
                class_name="empty-state",
            ),
        ),
        class_name="secondary-view",
    )


def _home_metric(icon: str, label: str, value: object, detail: str) -> rx.Component:
    return rx.box(
        rx.box(_icon(icon, 18), class_name="home-metric-icon"),
        rx.text(label, class_name="form-label"),
        rx.text(value, class_name="home-metric-value"),
        rx.text(detail, class_name="field-help"),
        class_name="home-metric",
    )


def _home_view() -> rx.Component:
    return rx.box(
        rx.heading("Policy workspace", size="7", class_name="page-title"),
        rx.text(
            "Create, preview, approve, and verify Gmail blocking policies from one local "
            "operator flow.",
            class_name="secondary-subtitle",
        ),
        rx.box(
            rx.hstack(
                rx.vstack(
                    rx.text("Current workflow", class_name="form-label"),
                    rx.heading(ConsoleState.status, size="5"),
                    rx.text(ConsoleState.change_summary, class_name="field-help"),
                    align="start",
                    spacing="1",
                ),
                rx.spacer(),
                rx.button(
                    "New blocked-sender policy",
                    on_click=ConsoleState.start_create("standard"),
                    class_name="secondary-action",
                ),
                rx.button(
                    "New compliance policy",
                    on_click=ConsoleState.start_create("compliance"),
                    class_name="primary-action",
                ),
                width="100%",
                class_name="home-actions",
            ),
            class_name="home-hero",
        ),
        rx.grid(
            _home_metric(
                "gauge",
                "Execution mode",
                ConsoleState.run_mode_label,
                "Change modes in the top bar or Settings.",
            ),
            _home_metric(
                "shield-check",
                "Managed policies",
                ConsoleState.managed_policies.length(),
                "Only resources with exact ownership evidence.",
            ),
            _home_metric(
                "file-check-2",
                "Audit packages",
                ConsoleState.audit_history.length(),
                "Terminal manifests checked on load.",
            ),
            _home_metric(
                "messages-square",
                "Orchestration",
                "4 specialists",
                "Microsoft Agent Framework group chat.",
            ),
            columns="2",
            gap="14px",
            class_name="home-metrics",
        ),
        class_name="secondary-view",
    )


def _main_view() -> rx.Component:
    return rx.cond(
        ConsoleState.active_view == "new_policy",
        _policy_editor(),
        rx.cond(
            ConsoleState.active_view == "home",
            _home_view(),
            rx.cond(
                ConsoleState.active_view == "runs",
                _runs_view(),
                rx.cond(
                    ConsoleState.active_view == "ownership",
                    _ownership_view(),
                    rx.cond(
                        ConsoleState.active_view == "audits",
                        _audits_view(),
                        _settings_view(),
                    ),
                ),
            ),
        ),
    )


def _evidence_strip() -> rx.Component:
    return rx.hstack(
        rx.box(_icon("shield-check", 16), class_name="evidence-strip-icon"),
        rx.box(class_name="strip-divider"),
        rx.button(
            "Session runs",
            _icon("chevron-right", 14),
            on_click=ConsoleState.select_view("runs"),
            class_name="strip-link",
        ),
        rx.button(
            "Integrity history",
            _icon("chevron-right", 14),
            on_click=ConsoleState.select_view("audits"),
            class_name="strip-link",
        ),
        rx.text("Evidence stays local and is hash-verified", class_name="strip-hint"),
        rx.box(class_name="strip-dash"),
        width="100%",
        class_name="evidence-strip",
    )


def index() -> rx.Component:
    return rx.box(
        _sidebar(),
        rx.box(
            _topbar(),
            rx.cond(
                ConsoleState.configuration_message != "",
                rx.hstack(
                    rx.cond(
                        ConsoleState.configuration_tone == "error",
                        _icon("triangle-alert", 15),
                        _icon("circle-check", 15),
                    ),
                    rx.text(ConsoleState.configuration_message),
                    class_name=rx.cond(
                        ConsoleState.configuration_tone == "error",
                        "configuration-banner error",
                        "configuration-banner success",
                    ),
                ),
            ),
            rx.grid(
                rx.box(_main_view(), class_name="main-pane"),
                _agent_rail(),
                columns="minmax(0, 1fr) 334px",
                class_name="content-grid",
            ),
            _evidence_strip(),
            class_name="workspace",
        ),
        class_name="app-shell",
    )


app = rx.App(stylesheets=["/styles.css"])
app.add_page(
    index,
    route="/",
    title="Gmail Policy Agent",
    on_load=ConsoleState.load_runtime_settings,
)
