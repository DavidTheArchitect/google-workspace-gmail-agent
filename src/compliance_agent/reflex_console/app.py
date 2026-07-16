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
            _nav_item("New policy", "square-plus", "new_policy"),
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
            rx.hstack(rx.box(class_name="model-ready-dot"), rx.text("Local model ready")),
            rx.text("Local · Gemma 4 12B", class_name="sidebar-meta"),
            rx.hstack(_icon("lock", 14), rx.text("No cloud processing")),
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
            _top_status("monitor", "Local · Gemma 4 12B"),
            _top_status("triangle-alert", "One approval required", "warning"),
            _top_status("lock", "No cloud processing"),
            class_name="topbar-statuses",
        ),
        rx.spacer(),
        rx.hstack(
            rx.text("Mode", class_name="mode-label"),
            rx.select(["Authoring", "Review"], default_value="Authoring"),
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
            default_value="Advanced",
            on_change=ConsoleState.set_expression_type_label,
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
            default_value="Full headers",
            on_change=ConsoleState.set_location_label,
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
            ],
            default_value="Matches regex",
            on_change=ConsoleState.set_match_type_label,
        ),
        rx.input(
            value=ConsoleState.expression_value,
            on_change=ConsoleState.set_expression_value,
            on_blur=ConsoleState.validate_expression,
            aria_label="Expression 1 value",
            class_name="expression-input",
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


def _additional_expression_row(row: object, index: object) -> rx.Component:
    return rx.grid(
        rx.text(index + 2, class_name="row-number"),
        rx.select(
            ["Advanced", "Simple", "Metadata", "Predefined"],
            default_value="Advanced",
            on_change=ConsoleState.update_expression(index, "type"),
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
            default_value="Subject",
            on_change=ConsoleState.update_expression(index, "location"),
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
            ],
            default_value="Contains",
            on_change=ConsoleState.update_expression(index, "match_type"),
        ),
        rx.input(
            value=row["value"],
            on_change=ConsoleState.update_expression(index, "value"),
            aria_label="Additional expression value",
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
    )


def _expression_details() -> rx.Component:
    return rx.cond(
        ConsoleState.expression_type == "metadata",
        rx.grid(
            rx.vstack(
                _field_label("Metadata attribute"),
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
                    value=ConsoleState.metadata_attribute,
                    on_change=ConsoleState.set_metadata_attribute,
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Metadata operator"),
                rx.input(
                    value=ConsoleState.metadata_operator,
                    on_change=ConsoleState.set_metadata_operator,
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
                columns="2",
                gap="16px",
                class_name="expression-details",
            ),
        ),
    )


def _compliance_editor() -> rx.Component:
    return rx.vstack(
        rx.vstack(
            _field_label("Organizational unit"),
            rx.select(
                ["/Finance", "/", "/Sales", "/Engineering", "/Operations"],
                value=ConsoleState.ou_path,
                on_change=ConsoleState.set_ou_path,
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
                ["Match ANY", "Match ALL"],
                default_value="Match ANY",
                on_change=ConsoleState.set_combiner_label,
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
        rx.grid(
            rx.vstack(
                _field_label("Organizational unit"),
                rx.select(
                    ["/Finance", "/", "/Sales", "/Engineering", "/Operations"],
                    value=ConsoleState.ou_path,
                    on_change=ConsoleState.set_ou_path,
                ),
                align="stretch",
                spacing="1",
            ),
            rx.vstack(
                _field_label("Rule label"),
                rx.input(value=ConsoleState.rule_name, on_change=ConsoleState.set_rule_name),
                align="stretch",
                spacing="1",
            ),
            columns="2",
            gap="16px",
            width="100%",
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


def _toolbar_button(icon: str, label: str) -> rx.Component:
    return rx.button(
        _icon(icon, 15),
        class_name="editor-tool",
        aria_label=label,
        title=label,
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
        rx.box(
            rx.hstack(
                rx.select(["Plain text"], default_value="Plain text"),
                _toolbar_button("bold", "Bold"),
                _toolbar_button("italic", "Italic"),
                _toolbar_button("underline", "Underline"),
                _toolbar_button("link", "Insert link"),
                _toolbar_button("list", "Bulleted list"),
                _toolbar_button("list-ordered", "Numbered list"),
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
                rx.select(["Insert variable"], default_value="Insert variable"),
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
                "Organizational unit", ConsoleState.ou_path, ConsoleState.ou_path, "No change"
            ),
            _impact_row(
                "Email messages to affect",
                "No agent-owned rule",
                ConsoleState.direction_summary,
                "Scoped",
            ),
            _impact_row(
                "Rules", "0 managed rules", ConsoleState.expression_count, "+1 rule", "good"
            ),
            _impact_row(
                "Estimated impact",
                "No messages affected",
                "Pending live read",
                "Evidence required",
                "warning",
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
                rx.hstack(_icon("file-check-2", 17), rx.text("Draft evidence ready")),
                rx.spacer(),
                rx.text(ConsoleState.plan_hash, class_name="evidence-hash"),
                width="100%",
            ),
            rx.text(
                "Approval remains locked until before-state and change-set hashes are bound.",
                class_name="evidence-copy",
            ),
            class_name="draft-evidence",
        ),
    )


def _policy_editor() -> rx.Component:
    return rx.box(
        rx.heading("Create Gmail policy", size="7", class_name="page-title"),
        _policy_tabs(),
        rx.cond(
            ConsoleState.section == "compliance",
            _compliance_editor(),
            _standard_editor(),
        ),
        _rejection_editor(),
        _impact_summary(),
        _draft_evidence(),
        rx.hstack(
            rx.spacer(),
            rx.button(
                rx.cond(
                    ConsoleState.review_in_progress,
                    "Reviewing with agents…",
                    "Review change",
                ),
                _icon("arrow-right", 17),
                on_click=ConsoleState.preview,
                disabled=ConsoleState.review_in_progress,
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
            rx.text("Agent group", class_name="agent-rail-title"),
            rx.hstack(_icon("users", 14), rx.text("4"), class_name="agent-count"),
            rx.spacer(),
            _icon("chevron-down", 17),
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
                rx.cond(ConsoleState.live_evidence_bound, "Approval ready", "1 approval required"),
                class_name="approval-state",
            ),
            rx.hstack(
                rx.hstack(
                    rx.avatar(fallback="AD", size="2"),
                    rx.text("Workspace Admin (you)"),
                ),
                rx.spacer(),
                rx.button(
                    rx.cond(ConsoleState.live_evidence_bound, "Approve", "Locked"),
                    on_click=ConsoleState.approve_plan,
                    disabled=~ConsoleState.live_evidence_bound,
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


def _main_view() -> rx.Component:
    return rx.cond(
        ConsoleState.active_view == "new_policy",
        _policy_editor(),
        rx.cond(
            ConsoleState.active_view == "home",
            _secondary_view(
                "Policy workspace",
                "Local-first Gmail policy operations with exact approval evidence.",
                (
                    (
                        "New policy",
                        "Create a sender block or Content compliance rejection.",
                        "Ready",
                    ),
                    (
                        "Latest run",
                        "No tenant mutation has been authorized in this session.",
                        "Safe",
                    ),
                    ("Model", "Gemma 4 · four-agent group chat", "Local"),
                ),
            ),
            rx.cond(
                ConsoleState.active_view == "runs",
                _secondary_view(
                    "Runs",
                    "Every run preserves planning, approval, browser, and verification evidence.",
                    (
                        ("Draft GW-1042", "Finance confidential marker guard", "Awaiting evidence"),
                        (
                            "Retention",
                            "Protected audit packages remain local for 90 days.",
                            "Active",
                        ),
                    ),
                ),
                rx.cond(
                    ConsoleState.active_view == "ownership",
                    _secondary_view(
                        "Managed ownership",
                        "Only visibly marked resources with matching local evidence can change.",
                        (
                            (
                                "Blocked sender rules",
                                "Application-owned rule and address-list pairs",
                                "Verified",
                            ),
                            (
                                "Content compliance",
                                "Managed rule names and exact OU bindings",
                                "Verified",
                            ),
                        ),
                    ),
                    rx.cond(
                        ConsoleState.active_view == "audits",
                        _secondary_view(
                            "Audit evidence",
                            "Hash-chained local records exclude credentials and page snapshots.",
                            (
                                (
                                    "Draft previews",
                                    "Plan, before-state, and change-set hashes",
                                    "Protected",
                                ),
                                (
                                    "Browser results",
                                    "Bounded semantic steps and verification outcome",
                                    "Redacted",
                                ),
                            ),
                        ),
                        _secondary_view(
                            "Settings",
                            "Runtime choices remain explicit and fail closed.",
                            (
                                ("Planning model", "gemma4:12b via local Ollama", "Local"),
                                (
                                    "Browser model",
                                    "Configurable vision model via CA_BROWSER_MODEL",
                                    "Local",
                                ),
                                (
                                    "Run mode",
                                    "Plan-only until identity and UI evidence are accepted",
                                    "Safe",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _evidence_strip() -> rx.Component:
    return rx.hstack(
        rx.box(_icon("arrow-down", 16), class_name="evidence-strip-icon"),
        rx.box(class_name="strip-divider"),
        rx.button(
            "Exact diff",
            _icon("chevron-right", 14),
            on_click=ConsoleState.select_view("audits"),
            class_name="strip-link",
        ),
        rx.button(
            "Audit evidence",
            _icon("chevron-right", 14),
            on_click=ConsoleState.select_view("audits"),
            class_name="strip-link",
        ),
        rx.text("Downstream sections continue below", class_name="strip-hint"),
        rx.box(class_name="strip-dash"),
        width="100%",
        class_name="evidence-strip",
    )


def index() -> rx.Component:
    return rx.box(
        _sidebar(),
        rx.box(
            _topbar(),
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
app.add_page(index, route="/", title="Gmail Policy Agent")
