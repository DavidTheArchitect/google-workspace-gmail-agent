"""Natural-language policy composition stays typed, local, and review-only."""

import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError
from pydantic import ValidationError

from compliance_agent.application.attended_policy_service import AttendedPolicyService
from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.group_chat import (
    PARTICIPANT_SPECS,
    GroupChatMessage,
    GroupChatTranscript,
)
from compliance_agent.llm.policy_draft import (
    POLICY_DRAFT_PROMPT_VERSION,
    PolicyDraftComposerResult,
    StructuredPolicyDraftComposer,
)
from compliance_agent.llm.structured import CompletionSampling
from compliance_agent.reflex_console.state import ConsoleState
from compliance_agent.schemas.compliance import (
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    EnvelopeFilter,
    ExpressionCombiner,
    MessageDirection,
    MetadataAttribute,
    MetadataMatch,
    OrganizationalUnitRef,
)
from compliance_agent.schemas.operations import RunMode
from compliance_agent.schemas.plan import CreateBlockedSenderRule, TaskPlan
from compliance_agent.schemas.policy_draft import (
    BlockedSendersDraft,
    ContentComplianceDraft,
    PolicyDraftAuditEvidence,
    PolicyDraftRecommendation,
)
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.settings import Settings


class FakeCompletionClient:
    """Return controlled model output and retain the schema-constrained calls."""

    def __init__(self, outputs: Sequence[str | Exception]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple] = []

    async def complete(
        self,
        messages: tuple,
        schema: dict,
        model: str,
        temperature: float,
        *,
        sampling: CompletionSampling | None = None,
    ) -> str:
        self.calls.append((messages, schema, model, temperature, sampling))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _standard_recommendation() -> PolicyDraftRecommendation:
    return PolicyDraftRecommendation(
        status="draft",
        selection=BlockedSendersDraft(
            target_ou="/",
            entries=(
                AddressEntry(kind="domain", value="Bad.Example"),
                AddressEntry(kind="email", value="sender@example.com"),
            ),
            bypass_entries=(AddressEntry(kind="domain", value="trusted.example"),),
            used_default_ou=True,
        ),
        routing_explanation="Literal sender identities fit Blocked senders exactly.",
        assumptions=(),
    )


def _compliance_recommendation() -> PolicyDraftRecommendation:
    return PolicyDraftRecommendation(
        status="draft",
        selection=ContentComplianceDraft(
            target_ou=OrganizationalUnitRef(path="/Finance"),
            directions=(MessageDirection.INBOUND,),
            combiner=ExpressionCombiner.ALL,
            expressions=(
                AdvancedContentMatch(
                    location=AdvancedContentLocation.SENDER_HEADER,
                    match_type=AdvancedMatchType.MATCHES_REGEX,
                    value=r"(?i)^invoice-[0-9]+@example\.com$",
                    regex_description="Invoice sender pattern",
                ),
                MetadataMatch(
                    attribute=MetadataAttribute.MESSAGE_AUTHENTICATION,
                    operator="not_authenticated",
                ),
            ),
            envelope_filters=(
                EnvelopeFilter(
                    party="recipient",
                    selector="single_address",
                    value="accounts@example.com",
                ),
            ),
            used_default_ou=False,
            used_default_directions=True,
        ),
        routing_explanation="Variable sender names require RE2 and authentication must also fail.",
        assumptions=(),
    )


def _broad_content_recommendation() -> PolicyDraftRecommendation:
    return PolicyDraftRecommendation(
        status="draft",
        selection=ContentComplianceDraft(
            target_ou=OrganizationalUnitRef(path="/"),
            directions=(MessageDirection.INBOUND,),
            combiner=ExpressionCombiner.ALL,
            expressions=(
                AdvancedContentMatch(
                    location=AdvancedContentLocation.HEADERS_AND_BODY,
                    match_type=AdvancedMatchType.CONTAINS,
                    value="roborock",
                ),
            ),
            used_default_ou=True,
            used_default_directions=True,
        ),
        routing_explanation=(
            "A literal word anywhere in an email uses Content compliance contains matching."
        ),
        assumptions=("Interpreting the unqualified email content as headers and body.",),
    )


def test_recommendation_contract_separates_drafts_from_terminal_outcomes() -> None:
    recommendation = _standard_recommendation()
    selection = recommendation.selection

    assert isinstance(selection, BlockedSendersDraft)
    assert selection.entries[0].normalized_value == "bad.example"
    assert recommendation.status == "draft"

    with pytest.raises(ValidationError, match="requires a selection"):
        PolicyDraftRecommendation(status="draft", routing_explanation="Missing selection")
    with pytest.raises(ValidationError, match="cannot contain a draft selection"):
        PolicyDraftRecommendation(
            status="clarification_needed",
            selection=selection,
            clarification_question="Which sender?",
        )
    with pytest.raises(ValidationError, match="terminal outcome text"):
        PolicyDraftRecommendation(
            status="draft",
            selection=selection,
            routing_explanation="Exact identities.",
            unsupported_reason="Not here.",
        )
    with pytest.raises(ValidationError, match="requires only a clarification"):
        PolicyDraftRecommendation(status="clarification_needed")
    with pytest.raises(ValidationError, match="requires only an unsupported"):
        PolicyDraftRecommendation(status="unsupported")
    assert (
        PolicyDraftRecommendation(
            status="unsupported",
            unsupported_reason="Routing actions are outside this composer.",
        ).status
        == "unsupported"
    )


def test_draft_contract_rejects_ambiguous_or_duplicate_shapes() -> None:
    domain = AddressEntry(kind="domain", value="example.com")
    expression = AdvancedContentMatch(
        location=AdvancedContentLocation.SUBJECT,
        match_type=AdvancedMatchType.CONTAINS,
        value="blocked",
    )

    with pytest.raises(ValidationError, match="at least one exact"):
        BlockedSendersDraft(entries=())
    with pytest.raises(ValidationError, match="duplicate normalized"):
        BlockedSendersDraft(entries=(domain, domain))
    with pytest.raises(ValidationError, match="blocked and bypassed"):
        BlockedSendersDraft(entries=(domain,), bypass_entries=(domain,))
    with pytest.raises(ValidationError, match="absolute normalized"):
        BlockedSendersDraft(target_ou="Sales", entries=(domain,))
    with pytest.raises(ValidationError, match="unique mail directions"):
        ContentComplianceDraft(
            target_ou=OrganizationalUnitRef(path="/"),
            directions=(MessageDirection.INBOUND, MessageDirection.INBOUND),
            combiner=ExpressionCombiner.ALL,
            expressions=(expression,),
        )
    with pytest.raises(ValidationError, match="between one and ten"):
        ContentComplianceDraft(
            target_ou=OrganizationalUnitRef(path="/"),
            directions=(MessageDirection.INBOUND,),
            combiner=ExpressionCombiner.ALL,
            expressions=(),
        )
    with pytest.raises(ValidationError, match="one envelope filter per party"):
        ContentComplianceDraft(
            target_ou=OrganizationalUnitRef(path="/"),
            directions=(MessageDirection.INBOUND,),
            combiner=ExpressionCombiner.ALL,
            expressions=(expression,),
            envelope_filters=(
                EnvelopeFilter(
                    party="sender",
                    selector="single_address",
                    value="first@example.com",
                ),
                EnvelopeFilter(
                    party="sender",
                    selector="single_address",
                    value="second@example.com",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_structured_composer_accepts_exact_standard_selection() -> None:
    expected = _standard_recommendation()
    client = FakeCompletionClient([expected.model_dump_json()])
    composer = StructuredPolicyDraftComposer(client, model="gemma4:12b")

    result = await composer.compose(
        "Block bad.example and sender@example.com except trusted.example",
        default_ou="/",
        default_directions=(MessageDirection.INBOUND,),
    )

    assert result.recommendation == expected
    assert result.prompt_template_version == POLICY_DRAFT_PROMPT_VERSION
    assert client.calls[0][3] == 0
    assert "PolicyDraftRecommendation" in json.dumps(client.calls[0][1])


@pytest.mark.asyncio
async def test_invalid_re2_retries_without_repairing_the_pattern() -> None:
    invalid = _compliance_recommendation().model_copy(
        update={
            "selection": _compliance_recommendation().selection.model_copy(
                update={
                    "expressions": (
                        AdvancedContentMatch(
                            location=AdvancedContentLocation.SENDER_HEADER,
                            match_type=AdvancedMatchType.MATCHES_REGEX,
                            value=r"(?<=invoice-)bad",
                        ),
                    )
                }
            )
        }
    )
    valid = _compliance_recommendation()
    client = FakeCompletionClient([invalid.model_dump_json(), valid.model_dump_json()])
    composer = StructuredPolicyDraftComposer(client, model="gemma4:12b", max_retries=1)

    result = await composer.compose(
        "Block invoice sender patterns",
        default_ou="/Finance",
        default_directions=(MessageDirection.INBOUND,),
    )

    assert result.recommendation == valid
    assert len(result.attempts) == 2
    assert "invalid Google RE2" in "\n".join(result.attempts[0].validation_errors)
    assert "Validation errors" in str(client.calls[1][0])


@pytest.mark.asyncio
async def test_composer_rejects_bad_input_before_calling_model() -> None:
    client = FakeCompletionClient([])
    composer = StructuredPolicyDraftComposer(client, model="gemma4:12b")

    with pytest.raises(PlannerFailure, match="blank"):
        await composer.compose(
            " ",
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )
    with pytest.raises(PlannerFailure, match="2000"):
        await composer.compose(
            "x" * 2_001,
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_composer_rejects_invalid_configuration_and_exhausted_output() -> None:
    client = FakeCompletionClient(["invalid"])
    with pytest.raises(ValueError, match="model tag"):
        StructuredPolicyDraftComposer(client, model=" ")
    with pytest.raises(ValueError, match="temperature"):
        StructuredPolicyDraftComposer(client, model="gemma", temperature=0.2)
    with pytest.raises(ValueError, match="max_retries"):
        StructuredPolicyDraftComposer(client, model="gemma", max_retries=4)

    composer = StructuredPolicyDraftComposer(client, model="gemma", max_retries=0)
    with pytest.raises(ValueError, match="unique message directions"):
        await composer.compose(
            "Block a subject",
            default_ou="/",
            default_directions=(),
        )
    with pytest.raises(PlannerFailure, match="remained invalid"):
        await composer.compose(
            "Block a subject",
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )


@pytest.mark.asyncio
async def test_composer_maps_connection_failure_without_changing_any_draft() -> None:
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    composer = StructuredPolicyDraftComposer(
        FakeCompletionClient([APIConnectionError(request=request)]),
        model="gemma",
    )

    with pytest.raises(PlannerFailure, match="existing policy draft was preserved"):
        await composer.compose(
            "Block example.com",
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )


@pytest.mark.asyncio
async def test_implicit_content_location_is_inferred_by_the_model() -> None:
    client = FakeCompletionClient([_broad_content_recommendation().model_dump_json()])
    composer = StructuredPolicyDraftComposer(client, model="gemma4:12b")

    result = await composer.compose(
        "block emails with the word 'roborock' in them.",
        default_ou="/",
        default_directions=(MessageDirection.INBOUND,),
    )

    selection = result.recommendation.selection
    assert isinstance(selection, ContentComplianceDraft)
    expression = selection.expressions[0]
    assert isinstance(expression, AdvancedContentMatch)
    assert expression.location == AdvancedContentLocation.HEADERS_AND_BODY
    assert expression.match_type == AdvancedMatchType.CONTAINS
    assert expression.value == "roborock"
    assert len(result.attempts) == 1
    assert len(client.calls) == 1
    assert client.calls[0][4].reasoning_effort == "none"


@pytest.mark.asyncio
async def test_location_only_clarification_is_retried_as_an_inferred_draft() -> None:
    clarification = PolicyDraftRecommendation(
        status="clarification_needed",
        clarification_question="Should this match the subject or body?",
    )
    client = FakeCompletionClient(
        [
            clarification.model_dump_json(),
            _broad_content_recommendation().model_dump_json(),
        ]
    )
    composer = StructuredPolicyDraftComposer(client, model="gemma4:12b", max_retries=1)

    result = await composer.compose(
        "block emails with the word 'roborock' in them.",
        default_ou="/",
        default_directions=(MessageDirection.INBOUND,),
    )

    assert result.recommendation.status == "draft"
    assert len(result.attempts) == 2
    assert "must be inferred as headers_and_body" in "\n".join(result.attempts[0].validation_errors)


@pytest.mark.asyncio
async def test_reflex_implicit_content_request_populates_a_reviewable_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recommendation = _broad_content_recommendation()

    class _Composer:
        async def compose(self, *_args: object, **_kwargs: object) -> PolicyDraftComposerResult:
            return PolicyDraftComposerResult(
                recommendation=recommendation,
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    async def _ready(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_policy_draft_composer",
        lambda _settings: _Composer(),
    )
    monkeypatch.setattr("compliance_agent.reflex_console.state.require_local_model", _ready)
    state = ConsoleState(_reflex_internal_init=True)
    state.blocked_values = "existing.example"
    state.composer_description = "block emails with the word 'roborock' in them."

    assert [update async for update in state.compose_policy()] == [None]
    assert state.composer_outcome == "ready"
    assert state.section == "compliance"
    assert state.location == "headers_and_body"
    assert state.match_type == "contains"
    assert state.expression_value == "roborock"
    assert state.blocked_values == "existing.example"
    assert not state.review_in_progress
    assert not state.browser_in_progress


@pytest.mark.asyncio
async def test_composer_reports_model_timeout_separately_from_invalid_output() -> None:
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    composer = StructuredPolicyDraftComposer(
        FakeCompletionClient([APITimeoutError(request=request)]),
        model="gemma",
    )

    with pytest.raises(PlannerFailure, match="timed out"):
        await composer.compose(
            "Block subjects containing roborock",
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )


@pytest.mark.asyncio
async def test_fenced_recommendation_and_envelope_regex_are_validated() -> None:
    recommendation = _compliance_recommendation()
    fenced = f"```json\n{recommendation.model_dump_json()}\n```"
    result = await StructuredPolicyDraftComposer(
        FakeCompletionClient([fenced]),
        model="gemma",
    ).compose(
        "Block invoice patterns",
        default_ou="/",
        default_directions=(MessageDirection.INBOUND,),
    )
    assert result.attempts[0].used_compatibility_extraction

    selection = recommendation.selection
    assert isinstance(selection, ContentComplianceDraft)
    invalid_envelope = recommendation.model_copy(
        update={
            "selection": selection.model_copy(
                update={
                    "envelope_filters": (
                        EnvelopeFilter(
                            party="sender",
                            selector="pattern",
                            value=r"(?<=bad)@example\.com",
                        ),
                    )
                }
            )
        }
    )
    composer = StructuredPolicyDraftComposer(
        FakeCompletionClient([invalid_envelope.model_dump_json()]),
        model="gemma",
        max_retries=0,
    )
    with pytest.raises(PlannerFailure, match="remained invalid"):
        await composer.compose(
            "Block an envelope sender pattern",
            default_ou="/",
            default_directions=(MessageDirection.INBOUND,),
        )


@pytest.mark.asyncio
async def test_reflex_composer_hydrates_standard_form_and_stops_before_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recommendation = _standard_recommendation()

    class _Composer:
        async def compose(self, *_args: object, **_kwargs: object) -> PolicyDraftComposerResult:
            return PolicyDraftComposerResult(
                recommendation=recommendation,
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    async def _ready(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_policy_draft_composer",
        lambda _settings: _Composer(),
    )
    monkeypatch.setattr("compliance_agent.reflex_console.state.require_local_model", _ready)
    state = ConsoleState(_reflex_internal_init=True)
    state.composer_description = "Block bad.example and sender@example.com"

    assert [update async for update in state.compose_policy()] == [None]

    assert state.section == "standard"
    assert state.blocked_values == "Bad.Example\nsender@example.com"
    assert state.bypass_values == "trusted.example"
    assert state.composer_surface_label == "Blocked senders"
    assert state.composer_outcome == "ready"
    assert state.composer_assumptions == ["Using the current organizational unit: /."]
    assert not state.preview_ready
    assert not state.review_in_progress
    assert not state.browser_in_progress
    evidence = state._composer_audit_evidence()
    assert evidence is not None
    assert not evidence.edited_after_application

    state.set_blocked_values("changed.example")
    edited_evidence = state._composer_audit_evidence()
    assert edited_evidence is not None
    assert edited_evidence.edited_after_application


@pytest.mark.asyncio
async def test_reflex_composer_hydrates_compliance_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recommendation = _compliance_recommendation()

    class _Composer:
        async def compose(self, *_args: object, **_kwargs: object) -> PolicyDraftComposerResult:
            return PolicyDraftComposerResult(
                recommendation=recommendation,
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    async def _ready(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_policy_draft_composer",
        lambda _settings: _Composer(),
    )
    monkeypatch.setattr("compliance_agent.reflex_console.state.require_local_model", _ready)
    state = ConsoleState(_reflex_internal_init=True)
    state.composer_description = "Block unauthenticated invoice sender patterns in Finance"

    assert [update async for update in state.compose_policy()] == [None]

    assert state.section == "compliance"
    assert state.ou_path == "/Finance"
    assert state.expression_value == r"(?i)^invoice-[0-9]+@example\.com$"
    assert state.expression_valid
    assert state.additional_expressions[0]["attribute"] == "message_authentication"
    assert state.recipient_filter_enabled
    assert state.recipient_filter_value == "accounts@example.com"
    assert state.composer_assumptions == ["Using the current message direction: inbound."]
    assert state._build_plan().actions[0].type == "create_content_compliance_rule"


@pytest.mark.asyncio
async def test_terminal_or_stale_composer_results_preserve_newer_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = PolicyDraftRecommendation(
        status="clarification_needed",
        clarification_question="Which exact sender pattern should be blocked?",
    )
    state = ConsoleState(_reflex_internal_init=True)
    state.blocked_values = "existing.example"
    state.composer_description = "Block this sender"

    class _TerminalComposer:
        async def compose(self, *_args: object, **_kwargs: object) -> PolicyDraftComposerResult:
            return PolicyDraftComposerResult(
                recommendation=terminal,
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    async def _ready(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("compliance_agent.reflex_console.state.require_local_model", _ready)
    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_policy_draft_composer",
        lambda _settings: _TerminalComposer(),
    )

    assert [update async for update in state.compose_policy()] == [None]
    assert state.blocked_values == "existing.example"
    assert state.composer_outcome == "clarification"

    class _StaleComposer:
        async def compose(self, *_args: object, **_kwargs: object) -> PolicyDraftComposerResult:
            state.set_blocked_values("newer.example")
            return PolicyDraftComposerResult(
                recommendation=_standard_recommendation(),
                model_tag="gemma4:12b",
                temperature=0,
                attempts=(),
            )

    state.composer_description = "Block bad.example"
    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_policy_draft_composer",
        lambda _settings: _StaleComposer(),
    )

    assert [update async for update in state.compose_policy()] == [None]
    assert state.blocked_values == "newer.example"
    assert state.composer_outcome == "discarded"


def test_reflex_create_page_exposes_review_only_composer() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "compliance_agent" / "reflex_console" / "app.py"
    ).read_text(encoding="utf-8")

    assert "Describe what to block" in source
    assert "ConsoleState.compose_policy" in source
    assert "It does not start review or access Google." in source
    assert "Draft generation in progress" in source
    assert 'ConsoleState.operation == "create"' in source


def test_composer_evidence_is_written_only_with_a_later_review(tmp_path: Path) -> None:
    settings = Settings(
        run_mode=RunMode.PLAN_ONLY,
        state_dir=tmp_path / "state",
        audit_dir=tmp_path / "audit",
    )
    plan = TaskPlan(
        status="plan",
        actions=(
            CreateBlockedSenderRule(
                entries=(AddressEntry(kind="domain", value="bad.example"),),
                rejection_notice="Mail refused by policy.",
            ),
        ),
    )
    transcript = GroupChatTranscript(
        participants=tuple(spec.name for spec in PARTICIPANT_SPECS),
        messages=tuple(
            GroupChatMessage(
                participant=spec.name,
                display_name=spec.display_name,
                icon=spec.icon,
                round_index=index,
                text="Reviewed the typed policy draft.",
            )
            for index, spec in enumerate(PARTICIPANT_SPECS)
        ),
        max_rounds=4,
    )
    service = AttendedPolicyService()

    abandoned_run_id = service.record_plan_review(settings, plan, transcript)
    abandoned_run = next(
        path
        for path in (settings.audit_dir / "runs").iterdir()
        if path.name.endswith(abandoned_run_id)
    )
    assert not (abandoned_run / "draft-composer.json").exists()

    evidence = PolicyDraftAuditEvidence(
        request_text="Block bad.example",
        recommendation=_standard_recommendation(),
        model_tag="gemma4:12b",
        prompt_template_version=POLICY_DRAFT_PROMPT_VERSION,
        edited_after_application=True,
    )
    reviewed_run_id = service.record_plan_review(
        settings,
        plan,
        transcript,
        composer_evidence=evidence,
    )
    reviewed_run = next(
        path
        for path in (settings.audit_dir / "runs").iterdir()
        if path.name.endswith(reviewed_run_id)
    )
    recorded = json.loads((reviewed_run / "draft-composer.json").read_text(encoding="utf-8"))

    assert recorded["request_text"] == "Block bad.example"
    assert recorded["model_tag"] == "gemma4:12b"
    assert recorded["prompt_template_version"] == POLICY_DRAFT_PROMPT_VERSION
    assert recorded["edited_after_application"] is True
