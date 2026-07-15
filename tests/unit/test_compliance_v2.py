"""Coverage for schema-v2 compliance blockers and local-agent boundaries."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import compliance_agent.llm.group_chat as group_chat_module
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.compliance_audit_service import ComplianceAuditService
from compliance_agent.application.compliance_ownership_service import (
    ComplianceOwnershipLifecycleService,
)
from compliance_agent.application.compliance_preview_service import (
    ComplianceApprovalService,
    CompliancePreviewService,
)
from compliance_agent.browser.navigation_agent import BrowserStep
from compliance_agent.browser.pages.content_compliance import (
    ComplianceBrowserPermit,
    ComplianceBrowserRunResult,
    _rule_inputs,
    _snapshot_matches_rule,
    _validate_permit,
)
from compliance_agent.browser.states import AdminPageState
from compliance_agent.domain.compliance_desired_state import (
    calculate_compliance_desired_state,
)
from compliance_agent.domain.diff import calculate_compliance_change_set
from compliance_agent.domain.ownership import (
    ComplianceOwnershipRecord,
    OwnershipRegistry,
    managed_compliance_rule_name,
    require_owned_compliance_rule,
)
from compliance_agent.domain.regex_validation import validate_google_regex
from compliance_agent.exceptions import AmbiguousTarget, OwnershipNotEstablished, StaleConfirmation
from compliance_agent.llm.group_chat import (
    GroupChatPlanner,
    _flatten_outputs,
    build_policy_group_chat,
)
from compliance_agent.llm.persona import PersonaNoticeGenerator
from compliance_agent.llm.structured import PlannerResult
from compliance_agent.reflex_console.state import ConsoleState
from compliance_agent.schemas.changes import ComplianceChangeSet
from compliance_agent.schemas.compliance import (
    AddressListCondition,
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    ContentComplianceRuleDraft,
    ContentComplianceState,
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
)
from compliance_agent.schemas.compliance_operations import ComplianceDryRunResult
from compliance_agent.schemas.plan import (
    AddBlockedEntries,
    CreateBlockedSenderRule,
    CreateContentComplianceRule,
    ListBlockedSenderRules,
    ListContentComplianceRules,
    RemoveContentComplianceRule,
    SetContentComplianceRuleEnabled,
    TaskPlan,
    UpdateContentComplianceRule,
)
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.results import ComplianceVerificationResult
from compliance_agent.settings import Settings

PREFIX = "[Compliance Agent]"
RULE_ID = UUID("10000000-0000-4000-8000-000000000001")
SECOND_ID = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 7, 15, tzinfo=UTC)


def _notice() -> GeneratedRejectionNotice:
    return GeneratedRejectionNotice(
        text="A library dragon declined this message under category policy (MAIL-204).",
        policy_category="category",
        policy_id="MAIL-204",
        persona=PersonaProfile(
            fictional_role="library dragon",
            traits=("curious", "precise"),
            voice="warm and concise",
            motif="paper cranes",
            seed=204,
        ),
    )


def _draft(*, inherited: bool = False) -> ContentComplianceRuleDraft:
    return ContentComplianceRuleDraft(
        target_ou=OrganizationalUnitRef(path="/Sales"),
        directions=(MessageDirection.INBOUND, MessageDirection.OUTBOUND),
        combiner=ExpressionCombiner.ALL,
        expressions=(
            AdvancedContentMatch(
                location=AdvancedContentLocation.FULL_HEADERS,
                match_type=AdvancedMatchType.MATCHES_REGEX,
                value=r"(?m)^X-Campaign: bad-[0-9]+$",
                regex_description="campaign marker",
            ),
        ),
        rejection_notice=_notice(),
        inherited=inherited,
    )


def _rule(rule_id: UUID = RULE_ID) -> ManagedContentComplianceRule:
    return ManagedContentComplianceRule(
        **_draft().model_dump(),
        ownership_id=rule_id,
        display_name=managed_compliance_rule_name(PREFIX, rule_id),
    )


def _registry(rule: ManagedContentComplianceRule | None = None) -> OwnershipRegistry:
    if rule is None:
        return OwnershipRegistry()
    return OwnershipRegistry(
        compliance_rules=(
            ComplianceOwnershipRecord(
                ownership_id=rule.ownership_id,
                display_name=rule.display_name,
                target_ou=rule.target_ou.path,
                created_at=NOW,
            ),
        )
    )


def test_schema_validates_ou_metadata_and_regex_shapes() -> None:
    assert OrganizationalUnitRef(path=" /Sales ").path == "/Sales"
    with pytest.raises(ValidationError):
        OrganizationalUnitRef(path="Sales")
    with pytest.raises(ValidationError):
        OrganizationalUnitRef(path="/Sales/")
    with pytest.raises(ValidationError):
        OrganizationalUnitRef(path="/../Sales")

    assert (
        MetadataMatch(
            attribute=MetadataAttribute.SOURCE_IP,
            operator="within_range",
            value="192.0.2.0/24",
        ).value
        == "192.0.2.0/24"
    )
    with pytest.raises(ValidationError):
        MetadataMatch(attribute=MetadataAttribute.SOURCE_IP, operator="within_range")
    with pytest.raises(ValidationError):
        MetadataMatch(
            attribute=MetadataAttribute.SECURE_TRANSPORT,
            operator="tls",
            value="unexpected",
        )
    with pytest.raises(ValidationError):
        MetadataMatch(attribute=MetadataAttribute.SECURE_TRANSPORT, operator="invalid")

    with pytest.raises(ValidationError):
        AdvancedContentMatch(
            location="body",
            match_type="is_empty",
            value="not allowed",
        )
    with pytest.raises(ValidationError):
        AdvancedContentMatch(location="body", match_type="contains")
    with pytest.raises(ValidationError):
        AdvancedContentMatch(
            location="body",
            match_type="contains",
            value="x",
            regex_description="invalid here",
        )


def test_re2_accepts_google_syntax_and_rejects_unsupported_syntax() -> None:
    assert validate_google_regex(r"^ok-[0-9]+$") == r"^ok-[0-9]+$"
    with pytest.raises(ValueError, match="1-10000"):
        validate_google_regex("")
    with pytest.raises(ValueError, match="1-10000"):
        validate_google_regex("a" * 10_001)
    with pytest.raises(ValueError, match="RE2"):
        validate_google_regex(r"(?<=secret)token")


def test_create_update_enable_remove_compliance_desired_state() -> None:
    create = TaskPlan(
        status="plan",
        actions=(CreateContentComplianceRule(rule=_draft()),),
    )
    created = calculate_compliance_desired_state(
        ContentComplianceState(), create, OwnershipRegistry(), (RULE_ID,), PREFIX
    )
    rule = created.rules[0]
    assert rule.display_name == managed_compliance_rule_name(PREFIX, RULE_ID)
    registry = _registry(rule)
    assert require_owned_compliance_rule(rule, registry, PREFIX).ownership_id == RULE_ID

    changed = rule.model_copy(
        update={
            "rejection_notice": _notice().model_copy(
                update={"text": "A changed category-only notice."}
            )
        }
    )
    update = TaskPlan(
        status="plan",
        actions=(UpdateContentComplianceRule(target_rule_id=RULE_ID, rule=changed),),
    )
    updated = calculate_compliance_desired_state(created, update, registry, (), PREFIX)
    assert updated.rules[0].rejection_notice.text == "A changed category-only notice."

    disable = TaskPlan(
        status="plan",
        actions=(SetContentComplianceRuleEnabled(target_rule_id=RULE_ID, enabled=False),),
    )
    disabled = calculate_compliance_desired_state(updated, disable, registry, (), PREFIX)
    assert not disabled.rules[0].enabled

    remove = TaskPlan(
        status="plan",
        actions=(RemoveContentComplianceRule(target_rule_id=RULE_ID),),
    )
    assert not calculate_compliance_desired_state(disabled, remove, registry, (), PREFIX).rules


def test_compliance_desired_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="injected ownership"):
        calculate_compliance_desired_state(
            ContentComplianceState(),
            TaskPlan(
                status="plan",
                actions=(CreateContentComplianceRule(rule=_draft()),),
            ),
            OwnershipRegistry(),
            (),
            PREFIX,
        )
    draft = _draft().model_copy(
        update={
            "expressions": (
                PredefinedContentMatch(
                    detector="Financial account number",
                    required_edition_capability="dlp_predefined_detectors",
                ),
            )
        }
    )
    unsupported_plan = TaskPlan(
        status="plan",
        actions=(CreateContentComplianceRule(rule=draft),),
    )
    with pytest.raises(AmbiguousTarget, match="capability"):
        calculate_compliance_desired_state(
            ContentComplianceState(),
            unsupported_plan,
            OwnershipRegistry(),
            (RULE_ID,),
            PREFIX,
        )
    with pytest.raises(OwnershipNotEstablished):
        require_owned_compliance_rule(_rule(), OwnershipRegistry(), PREFIX)


def test_compliance_desired_state_rejects_ambiguous_mutations() -> None:
    rule = _rule()
    registry = _registry(rule)
    state = ContentComplianceState(rules=(rule,))
    duplicate_create = TaskPlan(
        status="plan", actions=(CreateContentComplianceRule(rule=_draft()),)
    )
    with pytest.raises(ValueError, match="already exists"):
        calculate_compliance_desired_state(state, duplicate_create, registry, (RULE_ID,), PREFIX)

    moved = rule.model_copy(update={"target_ou": OrganizationalUnitRef(path="/Engineering")})
    with pytest.raises(AmbiguousTarget, match="moved between OUs"):
        calculate_compliance_desired_state(
            state,
            TaskPlan(
                status="plan",
                actions=(UpdateContentComplianceRule(target_rule_id=RULE_ID, rule=moved),),
            ),
            registry,
            (),
            PREFIX,
        )
    with pytest.raises(AmbiguousTarget, match="not observed"):
        calculate_compliance_desired_state(
            state,
            TaskPlan(
                status="plan",
                actions=(RemoveContentComplianceRule(target_rule_id=SECOND_ID),),
            ),
            registry,
            (),
            PREFIX,
        )

    inherited = rule.model_copy(update={"inherited": True})
    inherited_state = ContentComplianceState(rules=(inherited,))
    inherited_registry = _registry(inherited)
    actions = (
        UpdateContentComplianceRule.model_construct(
            target_rule_id=RULE_ID,
            rule=inherited,
            type="update_content_compliance_rule",
        ),
        RemoveContentComplianceRule(target_rule_id=RULE_ID),
        SetContentComplianceRuleEnabled(target_rule_id=RULE_ID, enabled=False),
    )
    for action in actions:
        with pytest.raises(AmbiguousTarget, match="inherited"):
            calculate_compliance_desired_state(
                inherited_state,
                TaskPlan.model_construct(
                    schema_version="2.0",
                    status="plan",
                    actions=(action,),
                    clarification_question=None,
                    unsupported_reason=None,
                ),
                inherited_registry,
                (),
                PREFIX,
            )


def test_compliance_desired_state_capability_list_and_name_collision() -> None:
    capability = "dlp_predefined_detectors"
    capable_draft = _draft().model_copy(
        update={
            "expressions": (
                PredefinedContentMatch(
                    detector="Financial account number",
                    required_edition_capability=capability,
                ),
            )
        }
    )
    state = ContentComplianceState(available_capabilities=frozenset({capability}))
    created = calculate_compliance_desired_state(
        state,
        TaskPlan(status="plan", actions=(CreateContentComplianceRule(rule=capable_draft),)),
        OwnershipRegistry(),
        (RULE_ID,),
        PREFIX,
    )
    listed = calculate_compliance_desired_state(
        created,
        TaskPlan(status="plan", actions=(ListContentComplianceRules(),)),
        _registry(created.rules[0]),
        (),
        PREFIX,
    )
    assert listed == created
    with pytest.raises(AmbiguousTarget, match="names are ambiguous"):
        calculate_compliance_desired_state(
            created.model_copy(update={"unmanaged_rule_names": (created.rules[0].display_name,)}),
            TaskPlan(status="plan", actions=(ListContentComplianceRules(),)),
            _registry(created.rules[0]),
            (),
            PREFIX,
        )


class _Ids:
    def __init__(self) -> None:
        self.values = iter((RULE_ID, SECOND_ID))

    def new(self) -> UUID:
        return next(self.values)


def test_compliance_change_service_and_diff() -> None:
    service = ChangeService(_Ids(), PREFIX)
    plan = TaskPlan(status="plan", actions=(CreateContentComplianceRule(rule=_draft()),))
    desired, changes = service.calculate_compliance(
        plan, ContentComplianceState(), OwnershipRegistry()
    )
    assert changes.rules_to_create == desired.rules
    assert changes.has_mutations
    assert not calculate_compliance_change_set(desired, desired).has_mutations


def test_compliance_preview_and_one_time_approval() -> None:
    plan = TaskPlan(status="plan", actions=(CreateContentComplianceRule(rule=_draft()),))
    preview = CompliancePreviewService(ChangeService(_Ids(), PREFIX)).preview(
        plan, ContentComplianceState(), OwnershipRegistry()
    )
    assert preview.status == "preview_ready"
    assert preview.impact is not None
    assert preview.impact.target_ous == ("/Sales",)
    approvals = ComplianceApprovalService(600)
    pending = approvals.issue("abcd1234", preview, NOW)
    with pytest.raises(ValueError, match="phrase"):
        approvals.approve(
            "abcd1234",
            phrase="wrong",
            acknowledged=True,
            approval_id="approval-1",
            now=NOW,
        )
    permit = approvals.approve(
        "abcd1234",
        phrase=pending.phrase,
        acknowledged=True,
        approval_id="approval-1",
        now=NOW,
    )
    assert permit.target_ou == "/Sales"
    assert permit.target_ownership_id == RULE_ID
    assert permit.operation == "create"
    with pytest.raises(ValueError, match="missing"):
        approvals.approve(
            "abcd1234",
            phrase=pending.phrase,
            acknowledged=True,
            approval_id="approval-2",
            now=NOW,
        )
    expired = approvals.issue("expired", preview, NOW)
    with pytest.raises(ValueError, match="expired"):
        approvals.approve(
            "expired",
            phrase=expired.phrase,
            acknowledged=True,
            approval_id="approval-expired",
            now=NOW + timedelta(seconds=601),
        )


class _Store:
    def __init__(self) -> None:
        self.registry = OwnershipRegistry()

    def load(self) -> OwnershipRegistry:
        return self.registry

    def save(self, registry: OwnershipRegistry) -> None:
        self.registry = registry


class _Clock:
    def now(self) -> datetime:
        return NOW


class _AuditWriter:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.artifacts: dict[str, str] = {}

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    def write_text(self, relative_path: str, content: str) -> Path:
        self.artifacts[relative_path] = content
        return Path(relative_path)

    def append(self, event: object) -> object:
        self.events.append(event)
        return event


def test_compliance_ownership_commits_only_matched_verification() -> None:
    rule = _rule()
    before = ContentComplianceState()
    after = ContentComplianceState(rules=(rule,))
    changes = ComplianceChangeSet(
        before_state=before,
        expected_after=after,
        rules_to_create=(rule,),
    )
    matched = ComplianceVerificationResult(
        status="matched", desired_state=after, observed_state=after
    )
    store = _Store()
    update = ComplianceOwnershipLifecycleService(store, _Clock()).commit_verified(changes, matched)
    assert update.added == (RULE_ID,)
    assert store.registry.find_compliance(RULE_ID) is not None
    mismatched = ComplianceVerificationResult(
        status="mismatched",
        desired_state=after,
        observed_state=before,
        differences=({"path": "rules", "kind": "missing"},),
    )
    assert (
        ComplianceOwnershipLifecycleService(store, _Clock())
        .commit_verified(changes, mismatched)
        .added
        == ()
    )


def test_compliance_ownership_conflict_removal_and_noop() -> None:
    rule = _rule()
    before = ContentComplianceState(rules=(rule,))
    empty = ContentComplianceState()
    removal = ComplianceChangeSet(
        before_state=before,
        expected_after=empty,
        rules_to_remove=(rule,),
    )
    store = _Store()
    store.registry = _registry(rule)
    matched_removal = ComplianceVerificationResult(
        status="matched", desired_state=empty, observed_state=empty
    )
    removed = ComplianceOwnershipLifecycleService(store, _Clock()).commit_verified(
        removal, matched_removal
    )
    assert removed.removed == (RULE_ID,)
    assert store.registry.find_compliance(RULE_ID) is None

    create = ComplianceChangeSet(
        before_state=empty,
        expected_after=ContentComplianceState(rules=(rule,)),
        rules_to_create=(rule,),
    )
    wrong = ComplianceVerificationResult(
        status="matched", desired_state=empty, observed_state=empty
    )
    with pytest.raises(OwnershipNotEstablished, match="expected state"):
        ComplianceOwnershipLifecycleService(store, _Clock()).commit_verified(create, wrong)

    store.registry = OwnershipRegistry(
        compliance_rules=(
            ComplianceOwnershipRecord(
                ownership_id=RULE_ID,
                display_name="conflicting",
                target_ou="/Sales",
                created_at=NOW,
            ),
        )
    )
    matched_create = ComplianceVerificationResult(
        status="matched", desired_state=create.expected_after, observed_state=create.expected_after
    )
    with pytest.raises(OwnershipNotEstablished, match="conflicts"):
        ComplianceOwnershipLifecycleService(store, _Clock()).commit_verified(create, matched_create)


def test_compliance_schema_rule_and_state_invariants() -> None:
    condition = AddressListCondition(mode="bypass", address_list_names=(" allowed ",))
    assert condition.address_list_names == ("allowed",)
    with pytest.raises(ValidationError, match="unique non-empty"):
        AddressListCondition(mode="bypass", address_list_names=("list", " list "))
    with pytest.raises(ValidationError, match="unique mail directions"):
        ContentComplianceRuleDraft(**(_draft().model_dump() | {"directions": ()}))
    with pytest.raises(ValidationError, match="between one and ten"):
        ContentComplianceRuleDraft(**(_draft().model_dump() | {"expressions": ()}))
    inherited = ContentComplianceRuleDraft(
        **(_draft().model_dump() | {"inherited": True, "enabled": False})
    )
    assert inherited.enabled
    with pytest.raises(ValidationError, match="duplicate resources"):
        ContentComplianceState(rules=(_rule(), _rule()))


def test_task_plan_rejects_ambiguous_or_terminal_action_shapes() -> None:
    entry = AddressEntry(kind="domain", value="example.test")
    with pytest.raises(ValidationError, match="cannot be blank"):
        CreateBlockedSenderRule(entries=(entry,), rejection_notice="   ")
    with pytest.raises(ValidationError, match="retain its ownership ID"):
        UpdateContentComplianceRule(target_rule_id=SECOND_ID, rule=_rule())
    inherited = _rule().model_copy(update={"inherited": True})
    with pytest.raises(ValidationError, match="inherited"):
        UpdateContentComplianceRule(target_rule_id=RULE_ID, rule=inherited)
    with pytest.raises(ValidationError, match="only action"):
        TaskPlan(
            status="plan",
            actions=(ListBlockedSenderRules(), AddBlockedEntries(entries=(entry,))),
        )
    with pytest.raises(ValidationError, match=r"schema 2\.0"):
        TaskPlan(
            schema_version="1.0",
            status="plan",
            actions=(CreateContentComplianceRule(rule=_draft()),),
        )
    with pytest.raises(ValidationError, match="at least one entry"):
        TaskPlan(status="plan", actions=(AddBlockedEntries(entries=()),))
    with pytest.raises(ValidationError, match="duplicate normalized"):
        TaskPlan(
            status="plan",
            actions=(AddBlockedEntries(entries=(entry, entry)),),
        )
    with pytest.raises(ValidationError, match="blocked and bypassed"):
        CreateBlockedSenderRule(entries=(entry,), bypass_entries=(entry,))
    with pytest.raises(ValidationError, match="absolute normalized"):
        AddBlockedEntries(entries=(entry,), target_ou="Sales")

    invalid_plans = (
        {"status": "plan"},
        {
            "status": "plan",
            "actions": (AddBlockedEntries(entries=(entry,)),),
            "unsupported_reason": "not terminal",
        },
        {
            "status": "unsupported",
            "actions": (AddBlockedEntries(entries=(entry,)),),
            "unsupported_reason": "terminal",
        },
        {"status": "clarification_needed"},
        {
            "status": "clarification_needed",
            "clarification_question": "Which OU?",
            "unsupported_reason": "conflict",
        },
        {"status": "unsupported"},
        {
            "status": "unsupported",
            "unsupported_reason": "unsupported",
            "clarification_question": "conflict",
        },
    )
    for raw in invalid_plans:
        with pytest.raises(ValidationError):
            TaskPlan.model_validate(raw)


def test_compliance_preview_and_verification_evidence_invariants() -> None:
    terminal = TaskPlan(status="unsupported", unsupported_reason="test")
    with pytest.raises(ValidationError, match="reason code"):
        ComplianceDryRunResult(status="blocked", plan=terminal, plan_hash="a" * 64)
    blocked = ComplianceDryRunResult(
        status="blocked",
        plan=terminal,
        plan_hash="a" * 64,
        reason_code="edition_capability_missing",
    )
    assert blocked.current_state is None
    with pytest.raises(ValidationError, match="complete evidence"):
        ComplianceDryRunResult(status="no_change", plan=terminal, plan_hash="a" * 64)

    state = ContentComplianceState()
    with pytest.raises(ValidationError, match="observed state"):
        ComplianceVerificationResult(status="matched", desired_state=state, observed_state=None)
    with pytest.raises(ValidationError, match="requires differences"):
        ComplianceVerificationResult(status="mismatched", desired_state=state, observed_state=state)
    with pytest.raises(ValidationError, match="cannot trust"):
        ComplianceVerificationResult(
            status="indeterminate",
            desired_state=state,
            observed_state=state,
            differences=({"path": "rules", "kind": "indeterminate"},),
        )


def test_compliance_audit_excludes_page_snapshot() -> None:
    plan = TaskPlan(status="plan", actions=(CreateContentComplianceRule(rule=_draft()),))
    preview = CompliancePreviewService(ChangeService(_Ids(), PREFIX)).preview(
        plan, ContentComplianceState(), OwnershipRegistry()
    )
    approvals = ComplianceApprovalService(600)
    pending = approvals.issue("audit-run", preview, NOW)
    permit = approvals.approve(
        "audit-run",
        phrase=pending.phrase,
        acknowledged=True,
        approval_id="approval-audit",
        now=NOW,
    )
    result = ComplianceBrowserRunResult(
        completed=True,
        verified=True,
        steps=("step",),
        final_page_state=AdminPageState.CONTENT_COMPLIANCE_RULE_EDITOR,
        final_snapshot="sensitive visible admin state",
    )
    writer = _AuditWriter()
    audit = ComplianceAuditService(writer, _Clock(), "audit-run")
    audit.record_preview(preview)
    audit.record_approval(permit)
    audit.record_browser_result(result, permit)
    assert len(writer.events) == 3
    assert "sensitive visible admin state" not in writer.artifacts["compliance-browser-result.json"]


class _Completion:
    def __init__(self, output: str) -> None:
        self.output = output

    async def complete(self, *_args: object, **_kwargs: object) -> str:
        return self.output


@pytest.mark.asyncio
async def test_persona_generator_accepts_bound_identity_and_falls_back() -> None:
    valid = _notice().model_copy(
        update={
            "persona": _notice().persona.model_copy(update={"seed": 42}),
            "used_fallback": False,
        }
    )
    generator = PersonaNoticeGenerator(
        _Completion(valid.model_dump_json()), model="gemma4:12b", temperature=0.9
    )
    # The random seed cannot match our fixed output, so immutable-field enforcement falls back.
    fallback = await generator.generate(policy_category="category", policy_id="MAIL-204")
    assert fallback.used_fallback
    assert fallback.disclosure == "category_only"


def test_browser_step_and_readback_helpers() -> None:
    assert BrowserStep(action="click", candidate_id="c001", rationale="Open").action == "click"
    assert BrowserStep(action="complete", rationale="Visible").candidate_id is None
    with pytest.raises(ValidationError):
        BrowserStep(action="fill", candidate_id="c001", rationale="Fill")
    rule = _rule()
    permit = ComplianceBrowserPermit(
        approval_id="approval-1",
        plan_hash="a" * 64,
        before_state_hash="b" * 64,
        change_set_hash="c" * 64,
        target_ou="/Sales",
        target_ownership_id=RULE_ID,
        operation="create",
        approved=True,
    )
    _validate_permit(rule, permit)
    with pytest.raises(StaleConfirmation):
        _validate_permit(rule, permit.model_copy(update={"target_ou": "/Other"}))
    inputs = _rule_inputs(rule)
    assert inputs[0].value == rule.display_name
    snapshot = (
        f"{rule.display_name}\n{rule.rejection_notice.text}\n/Sales\n"
        "Reject message\nall\ninbound outbound\nadvanced\nfull headers\nmatches regex\n"
        r"(?m)^X-Campaign: bad-[0-9]+$"
    )
    assert _snapshot_matches_rule(snapshot, rule)
    assert not _snapshot_matches_rule("missing", rule)


def test_browser_inputs_cover_metadata_predefined_lists_and_envelopes() -> None:
    draft = _draft().model_copy(
        update={
            "expressions": (
                MetadataMatch(
                    attribute=MetadataAttribute.SOURCE_IP,
                    operator="within_range",
                    value="192.0.2.0/24",
                ),
                PredefinedContentMatch(
                    detector="Financial account number",
                    minimum_match_count=2,
                    confidence="high",
                    required_edition_capability="dlp_predefined_detectors",
                ),
            ),
            "address_list_condition": AddressListCondition(
                mode="bypass", address_list_names=("trusted-senders",)
            ),
            "envelope_filters": (
                EnvelopeFilter(party="sender", selector="pattern", value="*@example.test"),
            ),
        }
    )
    rule = ManagedContentComplianceRule(
        **draft.model_dump(),
        ownership_id=RULE_ID,
        display_name=managed_compliance_rule_name(PREFIX, RULE_ID),
    )
    inputs = _rule_inputs(rule)
    labels = {item.label: item.value for item in inputs}
    assert labels["Expression 1 Attribute"] == "source_ip"
    assert labels["Expression 1 Operator"] == "within_range"
    assert labels["Expression 2 Detector"] == "Financial account number"
    assert labels["Address list 1"] == "trusted-senders"
    assert labels["Envelope filter 1 selector"] == "pattern"


def test_reflex_state_builds_both_plan_types_and_requires_live_read() -> None:
    state = ConsoleState(_reflex_internal_init=True)
    state.add_expression()
    state.update_expression("0", "value", "board-only")
    assert state.expression_count == 2
    assert state.additional_expressions[0]["value"] == "board-only"
    state.remove_expression("0")
    assert state.expression_count == 1
    assert state.additional_expressions == []

    compliance_plan = state._build_plan()
    assert isinstance(compliance_plan.actions[0], CreateContentComplianceRule)
    state.preview()
    assert state.preview_ready
    assert len(state.plan_hash) == 64
    state.acknowledged = True
    state.phrase_entry = state.approval_phrase
    state.approve_plan()
    assert not state.approved
    assert state.status == "Live read required"

    state.bind_live_evidence("a" * 64, "b" * 64)
    state.approve_plan()
    assert state.approved

    state.select_section("standard")
    state.blocked_values = "example.com\nsender@example.com"
    standard_plan = state._build_plan()
    assert standard_plan.actions[0].type == "create_blocked_sender_rule"
    state.match_type = "matches_regex"
    state.expression_value = r"(?<=bad)value"
    state.validate_expression()
    assert not state.expression_valid


def test_flatten_group_chat_outputs() -> None:
    class _Text:
        text = "review"

    assert _flatten_outputs([_Text(), ["second", " "]]) == ("review", "second")


@pytest.mark.asyncio
async def test_group_chat_planner_refines_then_calls_typed_planner() -> None:
    class _ReviewText:
        text = "review"

    class _Result:
        def get_outputs(self) -> list[object]:
            return ["architect", [_ReviewText(), " "]]

    class _Workflow:
        async def run(self, _message: object) -> _Result:
            return _Result()

    class _Planner:
        def __init__(self) -> None:
            self.request = ""

        async def plan(self, request: str) -> PlannerResult:
            self.request = request
            return PlannerResult(
                plan=TaskPlan(status="unsupported", unsupported_reason="test"),
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    planner = _Planner()
    result = await GroupChatPlanner(
        _Workflow(),  # type: ignore[arg-type]
        planner,  # type: ignore[arg-type]
        max_rounds=6,
    ).plan("block a header")
    assert result.transcript.messages == ("architect", "review")
    assert len(result.transcript.participants) == 4
    assert "block a header" in planner.request
    assert "architect" in planner.request


def test_build_policy_group_chat_wires_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            seen["client"] = kwargs

    class _Agent:
        def __init__(self, _client: object, **kwargs: object) -> None:
            seen.setdefault("agents", []).append(kwargs)  # type: ignore[union-attr]

    class _Builder:
        def __init__(self, **kwargs: object) -> None:
            seen["builder"] = kwargs

        def build(self) -> str:
            return "workflow"

    monkeypatch.setattr(group_chat_module, "OpenAIChatClient", _Client)
    monkeypatch.setattr(group_chat_module, "Agent", _Agent)
    monkeypatch.setattr(group_chat_module, "GroupChatBuilder", _Builder)
    assert build_policy_group_chat(Settings()) == "workflow"  # type: ignore[comparison-overlap]
    builder = seen["builder"]
    assert isinstance(builder, dict)
    selector = builder["selection_func"]

    class _State:
        current_round = 5

    assert callable(selector)
    assert selector(_State()) == "regex_reviewer"
    assert len(seen["agents"]) == 4  # type: ignore[arg-type]
