"""Agent-driven Gmail content-compliance UI automation with exact safety gates."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Self
from urllib.parse import urlparse
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field type at runtime.

from pydantic import Field, model_validator

from compliance_agent.browser.navigation_agent import (
    BrowserInput,
    BrowserObservation,
    GemmaBrowserNavigator,
    SemanticCatalog,
    execute_step,
)
from compliance_agent.browser.states import AdminPageState
from compliance_agent.exceptions import (
    RootOuNotConfirmed,
    SelectorNotFound,
    StaleConfirmation,
    UnknownPageState,
)
from compliance_agent.schemas.base import FrozenModel, Sha256Digest

if TYPE_CHECKING:
    from playwright.async_api import Page

    from compliance_agent.schemas.compliance import ManagedContentComplianceRule
    from compliance_agent.settings import Settings


class ComplianceBrowserPermit(FrozenModel):
    """One server-owned approval envelope consumed by an autonomous browser run."""

    approval_id: str = Field(min_length=1, max_length=200)
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest
    target_ou: str = Field(min_length=1, max_length=1_000)
    target_ownership_id: UUID
    operation: Literal["create", "update", "remove", "set_enabled"]
    approved: bool

    @model_validator(mode="after")
    def require_approval(self) -> Self:
        if not self.approved:
            message = "content-compliance browser permit must be explicitly approved"
            raise ValueError(message)
        return self


class ComplianceBrowserRunResult(FrozenModel):
    """Auditable result from one bounded create or update interaction."""

    completed: bool
    verified: bool
    steps: tuple[str, ...]
    final_page_state: AdminPageState
    final_snapshot: str = Field(max_length=30_000)


class ContentCompliancePage:
    """Detect and operate the Admin console compliance UI through semantic controls."""

    def __init__(
        self,
        page: Page,
        navigator: GemmaBrowserNavigator,
        *,
        candidate_limit: int,
        max_steps: int,
    ) -> None:
        self._page = page
        self._navigator = navigator
        self._candidate_limit = candidate_limit
        self._max_steps = max_steps
        self._consumed_approval_ids: set[str] = set()

    async def detect_state(self) -> AdminPageState:
        """Identify only known compliance page/editor semantics."""

        editor = self._page.get_by_role(
            "dialog", name=re.compile(r"(add|edit).*(setting|rule)", re.IGNORECASE)
        )
        if await editor.count() == 1:
            return AdminPageState.CONTENT_COMPLIANCE_RULE_EDITOR
        heading = self._page.get_by_role(
            "heading", name=re.compile(r"content compliance", re.IGNORECASE)
        )
        if await heading.count() == 1:
            return AdminPageState.CONTENT_COMPLIANCE_SECTION
        gmail_heading = self._page.get_by_role(
            "heading", name=re.compile(r"gmail.*(compliance|settings)", re.IGNORECASE)
        )
        if await gmail_heading.count() == 1:
            return AdminPageState.GMAIL_COMPLIANCE_SETTINGS
        return AdminPageState.UNKNOWN

    async def apply_rule(
        self,
        rule: ManagedContentComplianceRule,
        permit: ComplianceBrowserPermit,
    ) -> ComplianceBrowserRunResult:
        """Run one approved UI-only create/update and independently inspect the editor."""

        _validate_permit(rule, permit, allowed_operations=("create", "update"))
        self._consume_permit(permit)
        _require_admin_host(self._page.url)
        inputs = _rule_inputs(rule)
        goal = _apply_goal(rule)
        steps, completed = await self._run_goal(goal, inputs, permit)
        if not completed:
            state = await self.detect_state()
            snapshot = await self._aria_snapshot()
            return ComplianceBrowserRunResult(
                completed=False,
                verified=False,
                steps=steps,
                final_page_state=state,
                final_snapshot=snapshot,
            )
        await self._page.reload(wait_until="domcontentloaded")
        verification_goal = (
            f"Open the exact managed rule named {rule.display_name!r} for read-back. "
            "Do not save or change any field. Choose complete only when its editor is visible."
        )
        verify_steps, opened = await self._run_goal(
            verification_goal,
            (),
            permit,
            mutation_allowed=False,
        )
        snapshot = await self._aria_snapshot()
        return ComplianceBrowserRunResult(
            completed=True,
            verified=opened and _snapshot_matches_rule(snapshot, rule),
            steps=steps + verify_steps,
            final_page_state=await self.detect_state(),
            final_snapshot=snapshot,
        )

    async def remove_rule(
        self,
        rule: ManagedContentComplianceRule,
        permit: ComplianceBrowserPermit,
    ) -> ComplianceBrowserRunResult:
        """Remove one exact owned rule and verify its visible identity is absent."""

        _validate_permit(rule, permit, allowed_operations=("remove",))
        self._consume_permit(permit)
        _require_admin_host(self._page.url)
        goal = (
            "Open the exact managed Content compliance rule identified by the supplied tokens, "
            "verify its organizational unit, remove it through the visible Google Admin UI, "
            "confirm the removal once, and choose complete only after the rule list returns."
        )
        steps, completed = await self._run_goal(goal, _identity_inputs(rule), permit)
        await self._page.reload(wait_until="domcontentloaded")
        snapshot = await self._aria_snapshot()
        return ComplianceBrowserRunResult(
            completed=completed,
            verified=completed and rule.display_name.casefold() not in snapshot.casefold(),
            steps=steps,
            final_page_state=await self.detect_state(),
            final_snapshot=snapshot,
        )

    async def set_rule_enabled(
        self,
        rule: ManagedContentComplianceRule,
        *,
        enabled: bool,
        permit: ComplianceBrowserPermit,
    ) -> ComplianceBrowserRunResult:
        """Change one owned rule's enabled state and independently read it back."""

        _validate_permit(rule, permit, allowed_operations=("set_enabled",))
        self._consume_permit(permit)
        _require_admin_host(self._page.url)
        state_label = "enabled" if enabled else "disabled"
        inputs = (
            *_identity_inputs(rule),
            BrowserInput(input_id="i002", label="Enabled state", value=state_label),
        )
        goal = (
            "Open the exact managed Content compliance rule identified by the supplied tokens, "
            "set its supplied enabled state, save once, and choose complete only after the "
            "visible settings page returns."
        )
        steps, completed = await self._run_goal(goal, inputs, permit)
        await self._page.reload(wait_until="domcontentloaded")
        snapshot = await self._aria_snapshot()
        normalized = " ".join(snapshot.split()).casefold()
        verified = (
            completed and rule.display_name.casefold() in normalized and state_label in normalized
        )
        return ComplianceBrowserRunResult(
            completed=completed,
            verified=verified,
            steps=steps,
            final_page_state=await self.detect_state(),
            final_snapshot=snapshot,
        )

    def _consume_permit(self, permit: ComplianceBrowserPermit) -> None:
        if permit.approval_id in self._consumed_approval_ids:
            message = "content-compliance browser permit was already consumed"
            raise StaleConfirmation(message)
        self._consumed_approval_ids.add(permit.approval_id)

    async def _run_goal(
        self,
        goal: str,
        inputs: tuple[BrowserInput, ...],
        permit: ComplianceBrowserPermit,
        *,
        mutation_allowed: bool = True,
    ) -> tuple[tuple[str, ...], bool]:
        history: list[str] = []
        for _index in range(self._max_steps):
            _require_admin_host(self._page.url)
            state = await self.detect_state()
            if state is AdminPageState.UNKNOWN:
                message = "content-compliance page identity was not established"
                raise UnknownPageState(message)
            snapshot = await self._aria_snapshot()
            catalog = await SemanticCatalog.capture(self._page, limit=self._candidate_limit)
            observation = BrowserObservation(
                page_state=state,
                url=self._page.url,
                aria_snapshot=snapshot,
                candidates=catalog.candidates,
                inputs=inputs,
            )
            step = await self._navigator.choose_step(
                goal,
                observation,
                await self._page.screenshot(type="png"),
            )
            history.append(step.model_dump_json())
            if step.action == "complete":
                return tuple(history), True
            if not mutation_allowed and step.action in {
                "fill",
                "check",
                "uncheck",
                "select",
            }:
                message = "read-back browser goal proposed a mutation"
                raise SelectorNotFound(message)
            if step.candidate_id is not None:
                candidate = next(
                    (item for item in catalog.candidates if item.candidate_id == step.candidate_id),
                    None,
                )
                if candidate is not None and _is_commit_control(candidate.accessible_name):
                    if not mutation_allowed:
                        message = "read-back cannot activate a commit control"
                        raise SelectorNotFound(message)
                    _require_target_ou_visible(snapshot, permit.target_ou)
            await execute_step(self._page, catalog, step, inputs)
            await self._page.wait_for_timeout(250)
        return tuple(history), False

    async def _aria_snapshot(self) -> str:
        snapshot = await self._page.locator("body").aria_snapshot()
        return snapshot[:30_000]


def build_content_compliance_page(
    page: Page,
    settings: Settings,
) -> ContentCompliancePage:
    """Compose the bounded Admin UI operator with the selected local vision model."""

    navigator = GemmaBrowserNavigator(
        base_url=str(settings.ollama_base_url),
        model=settings.browser_model,
    )
    return ContentCompliancePage(
        page,
        navigator,
        candidate_limit=settings.browser_candidate_limit,
        max_steps=settings.browser_agent_max_steps,
    )


def _validate_permit(
    rule: ManagedContentComplianceRule,
    permit: ComplianceBrowserPermit,
    *,
    allowed_operations: tuple[str, ...] = ("create", "update", "remove", "set_enabled"),
) -> None:
    if permit.target_ou != rule.target_ou.path:
        message = "approved target OU no longer matches the compliance rule"
        raise StaleConfirmation(message)
    if permit.target_ownership_id != rule.ownership_id:
        message = "approved managed rule identity no longer matches"
        raise StaleConfirmation(message)
    if permit.operation not in allowed_operations:
        message = "approved compliance operation does not match the browser action"
        raise StaleConfirmation(message)


def _require_admin_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "admin.google.com":
        message = "browser left the approved Google Admin host"
        raise UnknownPageState(message)


def _require_target_ou_visible(snapshot: str, target_ou: str) -> None:
    normalized = " ".join(snapshot.split()).casefold()
    expected = "root organizational unit" if target_ou == "/" else target_ou
    if expected.casefold() not in normalized and target_ou.casefold() not in normalized:
        message = f"approved organizational unit is not visible before save: {target_ou}"
        raise RootOuNotConfirmed(message)


def _is_commit_control(name: str) -> bool:
    return bool(
        re.fullmatch(
            r"(save|add setting|update|apply|delete|remove|confirm)",
            name.strip(),
            re.IGNORECASE,
        )
    )


def _identity_inputs(rule: ManagedContentComplianceRule) -> tuple[BrowserInput, ...]:
    return (
        BrowserInput(input_id="i000", label="Managed rule name", value=rule.display_name),
        BrowserInput(input_id="i001", label="Organizational unit", value=rule.target_ou.path),
    )


def _rule_inputs(rule: ManagedContentComplianceRule) -> tuple[BrowserInput, ...]:
    values = [
        ("Managed rule name", rule.display_name),
        ("Organizational unit", rule.target_ou.path),
        ("Action", "Reject message"),
        ("Expression combiner", rule.combiner.value),
        ("Rejection notice", rule.rejection_notice.text),
    ]
    values.extend(
        (f"Message direction {index}", direction.value)
        for index, direction in enumerate(rule.directions, start=1)
    )
    for index, expression in enumerate(rule.expressions, start=1):
        expression_data = expression.model_dump(mode="json")
        for field_name in (
            "type",
            "location",
            "match_type",
            "content",
            "attribute",
            "operator",
            "value",
            "detector",
            "minimum_match_count",
            "confidence",
        ):
            field_value = expression_data.get(field_name)
            if field_value is not None:
                label = field_name.replace("_", " ").title()
                values.append((f"Expression {index} {label}", str(field_value)))
    if rule.address_list_condition is not None:
        values.append(("Address list condition mode", rule.address_list_condition.mode))
        values.extend(
            (f"Address list {index}", name)
            for index, name in enumerate(rule.address_list_condition.address_list_names, start=1)
        )
    for index, envelope_filter in enumerate(rule.envelope_filters, start=1):
        values.extend(
            (
                (f"Envelope filter {index} party", envelope_filter.party),
                (f"Envelope filter {index} selector", envelope_filter.selector),
                (f"Envelope filter {index} value", envelope_filter.value),
            )
        )
    return tuple(
        BrowserInput(input_id=f"i{index:03d}", label=label, value=value)
        for index, (label, value) in enumerate(values)
    )


def _apply_goal(rule: ManagedContentComplianceRule) -> str:
    return (
        "Using only the visible Google Admin UI, create or update the managed Gmail Content "
        "compliance setting using every supplied input token. Configure the exact OU, message "
        "directions, expression types, locations, operators, values, address-list conditions, "
        "and envelope filters in token order. Choose only the supplied Reject action, set the "
        "supplied rejection notice, and save once. Do not use quarantine, modify, route, APIs, "
        "or any value that is not supplied."
    )


def _snapshot_matches_rule(
    snapshot: str,
    rule: ManagedContentComplianceRule,
) -> bool:
    normalized = " ".join(snapshot.split()).casefold()
    required: list[str] = [
        rule.display_name,
        rule.rejection_notice.text,
        "Reject message",
        rule.combiner.value,
        ("root organizational unit" if rule.target_ou.path == "/" else rule.target_ou.path),
        *(direction.value.replace("_", " ") for direction in rule.directions),
    ]
    for expression in rule.expressions:
        expression_data = expression.model_dump(mode="json")
        required.extend(
            str(expression_data[field_name]).replace("_", " ")
            for field_name in (
                "type",
                "location",
                "match_type",
                "content",
                "attribute",
                "operator",
                "value",
                "detector",
                "minimum_match_count",
                "confidence",
            )
            if expression_data.get(field_name) is not None
        )
    if rule.address_list_condition is not None:
        required.append(rule.address_list_condition.mode.replace("_", " "))
        required.extend(rule.address_list_condition.address_list_names)
    for envelope_filter in rule.envelope_filters:
        required.extend(
            (
                envelope_filter.party,
                envelope_filter.selector.replace("_", " "),
                envelope_filter.value,
            )
        )
    return all(" ".join(value.split()).casefold() in normalized for value in required)
