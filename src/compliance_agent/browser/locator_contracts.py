"""Explicit semantic locator contracts for unique, scoped controls."""

import re
from typing import Literal, Self

from playwright.async_api import Locator, Page
from pydantic import model_validator

from compliance_agent.browser.states import AdminPageState
from compliance_agent.exceptions import SelectorAmbiguous, SelectorNotFound
from compliance_agent.schemas.base import FrozenModel

LocatorKind = Literal["role", "label", "text", "aria_label", "stable_attribute", "css_read_only"]
RoleName = Literal["button", "dialog", "heading", "textbox", "link", "row", "checkbox", "option"]
PostResolutionAssertion = Literal["visible", "enabled"]


class LocatorCandidate(FrozenModel):
    """One reviewed semantic locator candidate, not an arbitrary fallback selector."""

    kind: LocatorKind
    value: str
    role: RoleName | None = None
    attribute_name: str | None = None

    @model_validator(mode="after")
    def validate_strategy_fields(self) -> Self:
        if self.kind == "role" and self.role is None:
            message = "role locator candidates require a role"
            raise ValueError(message)
        if self.kind == "stable_attribute" and not self.attribute_name:
            message = "stable-attribute candidates require an attribute name"
            raise ValueError(message)
        if self.attribute_name and not re.fullmatch(
            r"[A-Za-z_:][A-Za-z0-9_.:-]*", self.attribute_name
        ):
            message = "stable-attribute name contains unsupported selector characters"
            raise ValueError(message)
        if self.kind == "stable_attribute" and not re.fullmatch(r"[A-Za-z0-9_.:-]+", self.value):
            message = "stable-attribute value contains unsupported selector characters"
            raise ValueError(message)
        return self


class LocatorContract(FrozenModel):
    """Safety assertions attached to one read or mutation locator purpose."""

    purpose: str
    allowed_page_states: tuple[AdminPageState, ...]
    expected_count: Literal[1] = 1
    mutation_capable: bool
    candidates: tuple[LocatorCandidate, ...]
    expected_role: RoleName | None = None
    expected_name_pattern: str | None = None
    container_role: RoleName | None = None
    container_name_pattern: str | None = None
    post_resolution_assertions: tuple[PostResolutionAssertion, ...] = ("visible",)

    @model_validator(mode="after")
    def reject_unsafe_mutation_candidates(self) -> Self:
        if not self.candidates:
            message = "locator contract requires at least one candidate"
            raise ValueError(message)
        if not self.allowed_page_states:
            message = "locator contract requires at least one allowed page state"
            raise ValueError(message)
        _validate_contract_patterns(self)
        if not self.mutation_capable:
            return self
        if self.container_role is None or self.container_name_pattern is None:
            message = "mutation locators require a semantic container"
            raise ValueError(message)
        prohibited = {"css_read_only", "text"}
        if any(candidate.kind in prohibited for candidate in self.candidates):
            message = "mutation locators cannot use structural CSS or broad text candidates"
            raise ValueError(message)
        if self.expected_role is None or self.expected_name_pattern is None:
            message = "mutation locators require expected role and accessible-name assertions"
            raise ValueError(message)
        matching_role_candidate = any(
            candidate.kind == "role"
            and candidate.role == self.expected_role
            and candidate.value == self.expected_name_pattern
            for candidate in self.candidates
        )
        if not matching_role_candidate:
            message = "mutation contract requires a role candidate matching its role/name assertion"
            raise ValueError(message)
        return self


def _validate_contract_patterns(contract: LocatorContract) -> None:
    patterns = [
        candidate.value
        for candidate in contract.candidates
        if candidate.kind in {"role", "label", "text", "aria_label"}
    ]
    patterns.extend(
        pattern
        for pattern in (contract.expected_name_pattern, contract.container_name_pattern)
        if pattern is not None
    )
    try:
        for pattern in patterns:
            re.compile(pattern)
    except re.error as error:
        message = f"locator contract contains an invalid regular expression: {error}"
        raise ValueError(message) from error


class LocatorSafetyContext(FrozenModel):
    """Preconditions established independently before a mutation control is resolved."""

    page_state: AdminPageState
    root_ou_confirmed: bool
    target_resource_confirmed: bool


async def resolve_locator(
    page: Page,
    contract: LocatorContract,
    safety: LocatorSafetyContext,
) -> Locator:
    """Resolve one reviewed contract or abort on state, ambiguity, or failed assertions."""

    if safety.page_state not in contract.allowed_page_states:
        message = f"{contract.purpose} is not allowed from page state {safety.page_state}"
        raise SelectorNotFound(message)
    if contract.mutation_capable and (
        not safety.root_ou_confirmed or not safety.target_resource_confirmed
    ):
        message = f"mutation preconditions are missing for {contract.purpose}"
        raise SelectorNotFound(message)
    container = await _resolve_container(page, contract)
    for candidate in contract.candidates:
        locator = _candidate_locator(container, candidate)
        count = await locator.count()
        if count > 1:
            message = f"{contract.purpose} candidate matched {count} elements"
            raise SelectorAmbiguous(message)
        if count == 1:
            await _assert_resolved(locator, contract)
            return locator
    message = f"no reviewed locator candidate matched {contract.purpose}"
    raise SelectorNotFound(message)


async def _resolve_container(page: Page, contract: LocatorContract) -> Page | Locator:
    if contract.container_role is None or contract.container_name_pattern is None:
        return page
    container = page.get_by_role(
        contract.container_role,
        name=re.compile(contract.container_name_pattern, re.IGNORECASE),
    )
    count = await container.count()
    if count > 1:
        message = f"{contract.purpose} container matched {count} elements"
        raise SelectorAmbiguous(message)
    if count == 0:
        message = f"{contract.purpose} container was not found"
        raise SelectorNotFound(message)
    return container


def _candidate_locator(container: Page | Locator, candidate: LocatorCandidate) -> Locator:
    pattern = re.compile(candidate.value, re.IGNORECASE)
    if candidate.kind == "role":
        if candidate.role is None:
            message = "validated role locator is missing its role"
            raise SelectorNotFound(message)
        return container.get_by_role(candidate.role, name=pattern)
    if candidate.kind == "label":
        return container.get_by_label(pattern)
    if candidate.kind == "text":
        return container.get_by_text(pattern)
    if candidate.kind == "aria_label":
        return container.get_by_label(pattern)
    if candidate.kind == "stable_attribute":
        if candidate.attribute_name is None:
            message = "validated stable-attribute locator is missing its attribute name"
            raise SelectorNotFound(message)
        safe_attribute = re.sub(r"[^a-zA-Z0-9_:-]", "", candidate.attribute_name)
        if safe_attribute != candidate.attribute_name:
            message = "stable attribute name contains unsupported characters"
            raise SelectorNotFound(message)
        return container.locator(f'[{safe_attribute}="{candidate.value}"]')
    return container.locator(candidate.value)


async def _assert_resolved(locator: Locator, contract: LocatorContract) -> None:
    if "visible" in contract.post_resolution_assertions and not await locator.is_visible():
        message = f"resolved locator is not visible: {contract.purpose}"
        raise SelectorNotFound(message)
    if "enabled" in contract.post_resolution_assertions and not await locator.is_enabled():
        message = f"resolved locator is not enabled: {contract.purpose}"
        raise SelectorNotFound(message)
