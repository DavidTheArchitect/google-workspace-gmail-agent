"""Focused integration tests for specialist attribution and browser action safety."""

import json
from unittest.mock import AsyncMock

import pytest
from agent_framework import AgentResponse, Message

import compliance_agent.browser.pages.content_compliance as content_module
from compliance_agent.application.compliance_browser_service import (
    ComplianceBrowserActionService,
)
from compliance_agent.browser.navigation_agent import BrowserCandidate, BrowserStep
from compliance_agent.browser.pages.content_compliance import (
    ComplianceBrowserPermit,
    ComplianceBrowserRunResult,
    ContentCompliancePage,
)
from compliance_agent.browser.states import AdminPageState
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.exceptions import SelectorNotFound, StaleConfirmation
from compliance_agent.llm.group_chat import PARTICIPANT_SPECS, _extract_messages
from compliance_agent.schemas.compliance import (
    AdvancedContentLocation,
    AdvancedContentMatch,
    AdvancedMatchType,
    ExpressionCombiner,
    GeneratedRejectionNotice,
    ManagedContentComplianceRule,
    MessageDirection,
    OrganizationalUnitRef,
    PersonaProfile,
)

RULE_ID = "11111111-1111-4111-8111-111111111111"


def _rule(*, enabled: bool = True) -> ManagedContentComplianceRule:
    notice = GeneratedRejectionNotice(
        text="Message rejected under the test policy.",
        policy_category="test",
        policy_id="MAIL-1",
        persona=PersonaProfile(
            fictional_role="Gatekeeper",
            traits=("clear",),
            voice="clear",
            motif="gate",
            seed=1,
        ),
    )
    return ManagedContentComplianceRule(
        ownership_id=RULE_ID,
        display_name="[Compliance Agent] test",
        target_ou=OrganizationalUnitRef(path="/Sales"),
        directions=(MessageDirection.INBOUND,),
        combiner=ExpressionCombiner.ANY,
        expressions=(
            AdvancedContentMatch(
                location=AdvancedContentLocation.FULL_HEADERS,
                match_type=AdvancedMatchType.MATCHES_REGEX,
                value=r"(?i)^X-Test: blocked$",
                regex_description="test marker",
            ),
        ),
        rejection_notice=notice,
        enabled=enabled,
    )


def _permit(
    operation: str = "create",
    rule: ManagedContentComplianceRule | None = None,
) -> ComplianceBrowserPermit:
    rule = rule or _rule()
    return ComplianceBrowserPermit(
        approval_id=f"approval-{operation}",
        plan_hash="a" * 64,
        before_state_hash="b" * 64,
        change_set_hash="c" * 64,
        target_rule_hash=canonical_hash(rule),
        target_ou=rule.target_ou.path,
        target_ownership_id=rule.ownership_id,
        operation=operation,  # type: ignore[arg-type]
        approved=True,
    )


def test_group_chat_output_preserves_framework_authors() -> None:
    outputs = [
        AgentResponse(
            messages=[
                Message(
                    role="assistant",
                    contents=[
                        json.dumps(
                            {
                                "verdict": "pass",
                                "summary": spec.name,
                                "findings": [],
                            }
                        )
                    ],
                    author_name=spec.name,
                )
            ]
        )
        for spec in PARTICIPANT_SPECS
    ]

    transcript = _extract_messages(outputs)

    assert [message.participant for message in transcript] == [
        spec.name for spec in PARTICIPANT_SPECS
    ]
    assert [message.round_index for message in transcript] == [0, 1, 2, 3]


def test_group_chat_ignores_unattributed_or_orchestrator_outputs() -> None:
    authored = [
        Message(
            role="assistant",
            contents=[
                json.dumps(
                    {"verdict": "pass", "summary": spec.name, "findings": []}
                )
            ],
            author_name=spec.name,
            message_id=f"turn-{index}",
        )
        for index, spec in enumerate(PARTICIPANT_SPECS)
    ]
    outputs: list[object] = [
        "unattributed",
        Message(role="user", contents=["operator"]),
        Message(
            role="assistant",
            contents=['{"verdict":"pass","summary":"ignore","findings":[]}'],
            author_name="policy_review_group",
        ),
        authored,
        authored[0],
    ]

    transcript = _extract_messages(outputs)

    assert [message.participant for message in transcript] == [
        spec.name for spec in PARTICIPANT_SPECS
    ]


class _CountLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class _BodyLocator:
    async def aria_snapshot(self) -> str:
        return "Content compliance /Sales"


class _FakePage:
    url = "https://admin.google.com/ac/apps/gmail/compliance"

    def get_by_role(self, role: str, **_kwargs: object) -> _CountLocator:
        return _CountLocator(1 if role == "heading" else 0)

    def locator(self, _selector: str) -> _BodyLocator:
        return _BodyLocator()

    async def screenshot(self, **_kwargs: object) -> bytes:
        return b"png"


class _Navigator:
    def __init__(self, step: BrowserStep) -> None:
        self._step = step

    async def choose_step(self, *_args: object) -> BrowserStep:
        return self._step


class _Catalog:
    def __init__(self, candidate: BrowserCandidate) -> None:
        self.candidates = (candidate,)


@pytest.mark.asyncio
async def test_readback_rejects_option_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = BrowserCandidate(
        candidate_id="c000",
        role="option",
        accessible_name="Enabled",
        allowed_actions=("click",),
    )
    monkeypatch.setattr(
        content_module.SemanticCatalog,
        "capture",
        AsyncMock(return_value=_Catalog(candidate)),
    )
    execute = AsyncMock()
    monkeypatch.setattr(content_module, "execute_step", execute)
    page = ContentCompliancePage(
        _FakePage(),  # type: ignore[arg-type]
        _Navigator(BrowserStep(action="click", candidate_id="c000", rationale="Select")),  # type: ignore[arg-type]
        candidate_limit=10,
        max_steps=3,
    )

    with pytest.raises(SelectorNotFound, match="read-back"):
        await page._run_goal("Inspect", (), _permit(), mutation_allowed=False)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_loop_stops_repeated_model_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = BrowserCandidate(
        candidate_id="c000",
        role="link",
        accessible_name="Rule",
        allowed_actions=("click",),
    )
    monkeypatch.setattr(
        content_module.SemanticCatalog,
        "capture",
        AsyncMock(return_value=_Catalog(candidate)),
    )
    execute = AsyncMock()
    monkeypatch.setattr(content_module, "execute_step", execute)
    page = ContentCompliancePage(
        _FakePage(),  # type: ignore[arg-type]
        _Navigator(BrowserStep(action="click", candidate_id="c000", rationale="Open")),  # type: ignore[arg-type]
        candidate_limit=10,
        max_steps=5,
    )

    with pytest.raises(SelectorNotFound, match="repeated"):
        await page._run_goal(
            "Open",
            (),
            _permit(),
            mutation_allowed=False,
            navigation_identity="Rule",
        )
    assert execute.await_count == 2


class _Operations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []
        self.result = ComplianceBrowserRunResult(
            completed=True,
            verified=True,
            steps=("complete",),
            final_page_state=AdminPageState.CONTENT_COMPLIANCE_SECTION,
            final_snapshot="verified",
        )

    async def apply_rule(self, *_args: object) -> ComplianceBrowserRunResult:
        self.calls.append(("apply", None))
        return self.result

    async def remove_rule(self, *_args: object) -> ComplianceBrowserRunResult:
        self.calls.append(("remove", None))
        return self.result

    async def set_rule_enabled(
        self, *_args: object, enabled: bool, **_kwargs: object
    ) -> ComplianceBrowserRunResult:
        self.calls.append(("set_enabled", enabled))
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "enabled", "expected"),
    [
        ("create", None, ("apply", None)),
        ("update", None, ("apply", None)),
        ("remove", None, ("remove", None)),
        ("set_enabled", False, ("set_enabled", False)),
    ],
)
async def test_browser_action_service_dispatches_only_permitted_operation(
    operation: str,
    enabled: bool | None,
    expected: tuple[str, bool | None],
) -> None:
    operations = _Operations()
    service = ComplianceBrowserActionService(lambda: operations)  # type: ignore[arg-type, return-value]
    rule = _rule(enabled=True if enabled is None else enabled)

    result = await service.execute(rule, _permit(operation, rule))

    assert result.verified
    assert operations.calls == [expected]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_browser_action_service_rejects_reused_permit() -> None:
    service = ComplianceBrowserActionService(_Operations)  # type: ignore[arg-type]
    rule = _rule()
    permit = _permit("create", rule)

    await service.execute(rule, permit)

    with pytest.raises(StaleConfirmation, match="already consumed"):
        await service.execute(rule, permit)
