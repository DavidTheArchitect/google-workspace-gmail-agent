"""Fail-closed locator-contract fixture tests without launching a browser."""

import pytest
from pydantic import ValidationError

from compliance_agent.browser.locator_contracts import (
    LocatorCandidate,
    LocatorContract,
    LocatorSafetyContext,
    resolve_locator,
)
from compliance_agent.browser.states import AdminPageState
from compliance_agent.exceptions import SelectorAmbiguous, SelectorNotFound


class FakeLocator:
    """Small locator double covering count and actionability assertions."""

    def __init__(self, count: int, *, visible: bool = True, enabled: bool = True) -> None:
        self._count = count
        self._visible = visible
        self._enabled = enabled

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible

    async def is_enabled(self) -> bool:
        return self._enabled

    def get_by_label(self, _pattern):
        return self

    def get_by_role(self, _role, name=None):
        return self

    def get_by_text(self, _pattern):
        return self

    def locator(self, _selector):
        return self

    def filter(self, **_kwargs):
        return self


class FakePage(FakeLocator):
    """Page double returning the configured locator for every reviewed strategy."""


def _mutation_contract() -> LocatorContract:
    return LocatorContract(
        purpose="save blocked sender rule",
        allowed_page_states=(AdminPageState.BLOCKED_SENDER_RULE_EDITOR,),
        mutation_capable=True,
        candidates=(LocatorCandidate(kind="role", role="button", value=r"^Save$"),),
        expected_role="button",
        expected_name_pattern=r"^Save$",
        container_role="dialog",
        container_name_pattern=r"^Edit blocked senders$",
        post_resolution_assertions=("visible", "enabled"),
    )


def _safety(**updates: object) -> LocatorSafetyContext:
    values = {
        "page_state": AdminPageState.BLOCKED_SENDER_RULE_EDITOR,
        "root_ou_confirmed": True,
        "target_resource_confirmed": True,
    }
    values.update(updates)
    return LocatorSafetyContext.model_validate(values)


def test_mutation_contract_rejects_css_broad_text_and_unscoped_candidates() -> None:
    base = {
        "purpose": "save",
        "allowed_page_states": [AdminPageState.BLOCKED_SENDER_RULE_EDITOR],
        "mutation_capable": True,
        "expected_role": "button",
        "expected_name_pattern": "Save",
        "container_role": "dialog",
        "container_name_pattern": "Edit",
    }
    for kind in ("css_read_only", "text"):
        with pytest.raises(ValidationError):
            LocatorContract.model_validate(
                {**base, "candidates": [{"kind": kind, "value": "button"}]}
            )
    with pytest.raises(ValidationError, match="semantic container"):
        LocatorContract.model_validate(
            {
                **base,
                "container_role": None,
                "container_name_pattern": None,
                "candidates": [{"kind": "role", "role": "button", "value": "Save"}],
            }
        )
    with pytest.raises(ValidationError, match="matching its role/name"):
        LocatorContract.model_validate(
            {
                **base,
                "candidates": [{"kind": "role", "role": "button", "value": "Delete"}],
            }
        )
    with pytest.raises(ValidationError, match="selector characters"):
        LocatorCandidate(
            kind="stable_attribute",
            attribute_name="data-action",
            value='save"] button',
        )
    with pytest.raises(ValidationError, match="invalid regular expression"):
        LocatorContract.model_validate(
            {
                **base,
                "candidates": [{"kind": "role", "role": "button", "value": "["}],
                "expected_name_pattern": "[",
            }
        )
    invalid_assertion = _mutation_contract().model_dump()
    invalid_assertion["post_resolution_assertions"] = ["visble"]
    with pytest.raises(ValidationError):
        LocatorContract.model_validate(invalid_assertion)


@pytest.mark.asyncio
async def test_unique_actionable_control_resolves_only_after_all_safety_checks() -> None:
    locator = await resolve_locator(FakePage(1), _mutation_contract(), _safety())
    assert await locator.count() == 1

    with pytest.raises(SelectorNotFound, match="preconditions"):
        await resolve_locator(
            FakePage(1),
            _mutation_contract(),
            _safety(root_ou_confirmed=False),
        )


@pytest.mark.asyncio
async def test_zero_duplicate_hidden_or_disabled_controls_abort() -> None:
    contract = _mutation_contract()
    with pytest.raises(SelectorNotFound):
        await resolve_locator(FakePage(0), contract, _safety())
    with pytest.raises(SelectorAmbiguous):
        await resolve_locator(FakePage(2), contract, _safety())
    with pytest.raises(SelectorNotFound, match="not visible"):
        await resolve_locator(FakePage(1, visible=False), contract, _safety())
    with pytest.raises(SelectorNotFound, match="not enabled"):
        await resolve_locator(FakePage(1, enabled=False), contract, _safety())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "value", "attribute_name"),
    [
        ("label", r"^Name$", None),
        ("text", r"^Read only text$", None),
        ("aria_label", r"^Name$", None),
        ("stable_attribute", "save", "data-action"),
        ("css_read_only", ".read-only", None),
    ],
)
async def test_reviewed_read_only_locator_strategies_resolve(
    kind: str,
    value: str,
    attribute_name: str | None,
) -> None:
    contract = LocatorContract.model_validate(
        {
            "purpose": "read fixture",
            "allowed_page_states": [AdminPageState.GMAIL_SPAM_SETTINGS],
            "mutation_capable": False,
            "candidates": [{"kind": kind, "value": value, "attribute_name": attribute_name}],
        }
    )
    safety = LocatorSafetyContext(
        page_state=AdminPageState.GMAIL_SPAM_SETTINGS,
        root_ou_confirmed=False,
        target_resource_confirmed=False,
    )

    assert await resolve_locator(FakePage(1), contract, safety)


@pytest.mark.asyncio
async def test_read_only_locator_rejects_wrong_state_and_ambiguous_candidate() -> None:
    contract = LocatorContract(
        purpose="read fixture",
        allowed_page_states=(AdminPageState.GMAIL_SPAM_SETTINGS,),
        mutation_capable=False,
        candidates=(LocatorCandidate(kind="text", value="fixture"),),
    )
    wrong_state = LocatorSafetyContext(
        page_state=AdminPageState.UNKNOWN,
        root_ou_confirmed=False,
        target_resource_confirmed=False,
    )
    correct_state = wrong_state.model_copy(
        update={"page_state": AdminPageState.GMAIL_SPAM_SETTINGS}
    )

    with pytest.raises(SelectorNotFound, match="not allowed"):
        await resolve_locator(FakePage(1), contract, wrong_state)
    with pytest.raises(SelectorAmbiguous, match="matched 2"):
        await resolve_locator(FakePage(2), contract, correct_state)
