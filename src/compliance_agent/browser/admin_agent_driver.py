"""Attended, agent-navigated Google Admin reader and writer."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Literal, Self
from urllib.parse import urlparse
from uuid import UUID  # noqa: TC003 - Pydantic resolves permit fields.

from pydantic import Field, model_validator

from compliance_agent.browser.navigation_agent import (
    BrowserCandidate,
    BrowserInput,
    BrowserObservation,
    GemmaBrowserNavigator,
    SemanticCatalog,
    execute_step,
)
from compliance_agent.browser.pages.content_compliance import (
    ComplianceBrowserPermit,
    ContentCompliancePage,
)
from compliance_agent.browser.pages.gmail_spam_settings import GmailSpamSettingsPage
from compliance_agent.browser.session import BrowserSession
from compliance_agent.browser.states import AdminPageState
from compliance_agent.browser.ui_extraction import (
    AdminIdentityObservation,
    AdminVisionExtractor,
    ObservedAddressList,
    ObservedBlockedSenderRule,
    ObservedComplianceExpression,
    ObservedContentComplianceRule,
    ObservedPredefinedContentMatch,
    VisiblePolicyIndex,
)
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.exceptions import (
    RootOuNotConfirmed,
    SelectorNotFound,
    StaleConfirmation,
    UnknownPageState,
)
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.compliance import (
    ComplianceExpression,
    ContentComplianceState,
    ManagedContentComplianceRule,
    PredefinedContentMatch,
)
from compliance_agent.schemas.resources import (
    AddressEntry,
    ManagedAddressList,
    ManagedBlockedSenderRule,
)
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.version import __version__

if TYPE_CHECKING:
    from datetime import datetime
    from types import TracebackType

    from playwright.async_api import Page

    from compliance_agent.schemas.changes import ChangeSet, ComplianceChangeSet
    from compliance_agent.settings import Settings

_REPEATED_STEP_LIMIT = 3


class AdminBrowserPermit(FrozenModel):
    """One exact, server-owned permission for a Google Admin mutation."""

    approval_id: str = Field(min_length=1, max_length=200)
    plan_hash: Sha256Digest
    before_state_hash: Sha256Digest
    change_set_hash: Sha256Digest
    target_ou: str = Field(min_length=1, max_length=1_000)
    target_ownership_id: UUID
    surface: Literal["blocked_senders", "content_compliance"]
    approved: bool

    @model_validator(mode="after")
    def require_approval(self) -> Self:
        if not self.approved:
            message = "Google Admin browser permit must be explicitly approved"
            raise ValueError(message)
        return self


class AdminBrowserApplyResult(FrozenModel):
    """Bounded browser-operation facts; verification is a separate fresh read."""

    completed: bool
    steps: tuple[str, ...]
    final_page_state: AdminPageState


class PlaywrightAdminAgentSession:
    """One headed browser session for read-only preview or approved execution."""

    def __init__(self, settings: Settings, *, run_id: str, started_at: datetime) -> None:
        self._settings = settings
        self._lock = ProcessLock(
            settings.state_dir / "run.lock",
            run_id=run_id,
            started_at=started_at,
            application_version=__version__,
        )
        self._session = BrowserSession(settings, self._lock)
        self._page: Page | None = None
        self._navigator = GemmaBrowserNavigator(
            base_url=str(settings.ollama_base_url),
            model=settings.browser_model,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
        self._extractor = AdminVisionExtractor(
            base_url=str(settings.ollama_base_url),
            model=settings.browser_model,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )

    async def __aenter__(self) -> PlaywrightAdminAgentSession:
        await self._session.__aenter__()
        self._page = self._session.page
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._page = None
        await self._session.__aexit__(exception_type, exception, traceback)

    async def read_blocked_sender_state(
        self,
        expected: BlockedSenderState,
        *,
        managed_prefix: str,
    ) -> BlockedSenderState:
        """Read every expected managed blocked-sender rule and linked list."""

        page = self._require_page()
        await self._open_admin_url(str(self._settings.gmail_settings_url))
        index = await self._extractor.extract(
            page,
            VisiblePolicyIndex,
            "List every visible Blocked senders setting name on this surface.",
        )
        if index.surface != "blocked_senders":
            message = "browser model did not establish the Blocked senders surface"
            raise UnknownPageState(message)
        observed_rules: list[ManagedBlockedSenderRule] = []
        observed_lists: dict[str, ManagedAddressList] = {}
        expected_lists = {item.display_name: item for item in expected.address_lists}
        for template in expected.rules:
            await self._open_admin_url(str(self._settings.gmail_settings_url))
            await self._run_goal(
                (
                    f"Open the exact Blocked senders setting named {template.display_name!r}. "
                    "Expand all persisted fields but do not change anything. Choose complete only "
                    "when the setting editor and its organizational unit are visible."
                ),
                inputs=(),
                mutation_allowed=False,
                target_ou=template.target_ou,
                navigation_identity=template.display_name,
            )
            page = self._require_page()
            observed = await self._extractor.extract(
                page,
                ObservedBlockedSenderRule,
                "Transcribe every field in the open Blocked senders setting.",
            )
            _require_expected_identity(observed.display_name, template.display_name, "rule")
            _require_expected_identity(
                observed.target_ou,
                template.target_ou,
                "organizational unit",
            )
            observed_rules.append(
                template.model_copy(
                    update={
                        "address_list_names": observed.blocked_list_names,
                        "bypass_address_list_names": observed.bypass_list_names,
                        "rejection_notice": observed.rejection_notice,
                        "enabled": observed.enabled,
                        "inherited": observed.inherited,
                    }
                )
            )
            for list_name in observed.blocked_list_names + observed.bypass_list_names:
                list_template = expected_lists.get(list_name)
                if list_template is None:
                    message = f"managed rule references an untracked address list: {list_name}"
                    raise StaleConfirmation(message)
                if list_name not in observed_lists:
                    observed_lists[list_name] = await self._read_address_list(list_template)
        expected_names = {rule.display_name for rule in expected.rules}
        unmanaged = tuple(
            sorted(
                name
                for name in index.rule_names
                if name not in expected_names or not name.startswith(f"{managed_prefix} ")
            )
        )
        return BlockedSenderState(
            target_ou=expected.target_ou,
            rules=tuple(observed_rules),
            address_lists=tuple(
                sorted(observed_lists.values(), key=lambda item: item.display_name)
            ),
            unmanaged_rule_names=unmanaged,
        )

    async def read_content_compliance_state(
        self,
        expected: ContentComplianceState,
        *,
        managed_prefix: str,
    ) -> ContentComplianceState:
        """Read every expected managed Content compliance rule from its editor."""

        page = self._require_page()
        await self._open_admin_url(str(self._settings.gmail_compliance_url))
        index = await self._extractor.extract(
            page,
            VisiblePolicyIndex,
            (
                "List every visible Content compliance setting name and only edition "
                "capabilities explicitly available on this surface."
            ),
        )
        if index.surface != "content_compliance":
            message = "browser model did not establish the Content compliance surface"
            raise UnknownPageState(message)
        observed_rules: list[ManagedContentComplianceRule] = []
        for template in expected.rules:
            await self._open_admin_url(str(self._settings.gmail_compliance_url))
            await self._run_goal(
                (
                    f"Open the exact Content compliance setting named {template.display_name!r}. "
                    "Expand every expression, option, and Reject message field without changing "
                    "anything. Choose complete only when all persisted fields are visible."
                ),
                inputs=(),
                mutation_allowed=False,
                target_ou=template.target_ou.path,
                navigation_identity=template.display_name,
            )
            page = self._require_page()
            observed = await self._extractor.extract(
                page,
                ObservedContentComplianceRule,
                "Transcribe every persisted field in the open Content compliance setting.",
            )
            _require_expected_identity(observed.display_name, template.display_name, "rule")
            _require_expected_identity(
                observed.target_ou,
                template.target_ou.path,
                "organizational unit",
            )
            observed_rules.append(
                template.model_copy(
                    update={
                        "directions": observed.directions,
                        "combiner": observed.combiner,
                        "expressions": _restore_local_expression_metadata(
                            observed.expressions,
                            template.expressions,
                        ),
                        "rejection_notice": template.rejection_notice.model_copy(
                            update={"text": observed.rejection_notice}
                        ),
                        "address_list_condition": observed.address_list_condition,
                        "envelope_filters": observed.envelope_filters,
                        "enabled": observed.enabled,
                        "inherited": observed.inherited,
                    }
                )
            )
        expected_names = {rule.display_name for rule in expected.rules}
        unmanaged = tuple(
            sorted(
                name
                for name in index.rule_names
                if name not in expected_names or not name.startswith(f"{managed_prefix} ")
            )
        )
        return ContentComplianceState(
            rules=tuple(observed_rules),
            unmanaged_rule_names=unmanaged,
            available_capabilities=index.available_capabilities,
        )

    async def apply_blocked_sender_change(
        self,
        change_set: ChangeSet,
        permit: AdminBrowserPermit,
    ) -> AdminBrowserApplyResult:
        """Apply one exact standard-blocking change set through visible Google controls."""

        _validate_admin_permit(change_set, permit, surface="blocked_senders")
        history: list[str] = []
        phases = (
            ("create", change_set.address_lists_to_create, "address list"),
            ("update", change_set.address_lists_to_update, "address list"),
            ("create", change_set.rules_to_create, "blocked-sender rule"),
            ("update", change_set.rules_to_update, "blocked-sender rule"),
            ("remove", change_set.rules_to_remove, "blocked-sender rule"),
            ("remove", change_set.address_lists_to_remove, "address list"),
        )
        for operation, resources, resource_kind in phases:
            for resource in resources:
                await self._open_admin_url(str(self._settings.gmail_settings_url))
                inputs = (
                    _address_list_inputs(operation, resource)
                    if isinstance(resource, ManagedAddressList)
                    else _blocked_rule_inputs(operation, resource)
                )
                identity = resource.display_name
                goal = (
                    f"{operation.title()} the exact managed Gmail {resource_kind} using every "
                    "supplied token. For updates, replace the complete editable value set; do "
                    "not merge omitted entries. Preserve every other setting, save exactly once, "
                    f"and choose complete only when {identity!r} is visibly persisted."
                )
                steps, completed = await self._run_goal(
                    goal,
                    inputs=inputs,
                    mutation_allowed=True,
                    target_ou=permit.target_ou,
                    commit_identity=identity,
                    commit_requires_ou=isinstance(resource, ManagedBlockedSenderRule),
                )
                history.extend(steps)
                if not completed:
                    return AdminBrowserApplyResult(
                        completed=False,
                        steps=tuple(history),
                        final_page_state=await self._detect_state(),
                    )
        return AdminBrowserApplyResult(
            completed=True,
            steps=tuple(history),
            final_page_state=await self._detect_state(),
        )

    async def apply_content_compliance_change(
        self,
        change_set: ComplianceChangeSet,
        permit: AdminBrowserPermit,
    ) -> AdminBrowserApplyResult:
        """Apply exactly one approved advanced blocker with the bounded page agent."""

        _validate_admin_permit(change_set, permit, surface="content_compliance")
        touched = (
            change_set.rules_to_create
            + change_set.rules_to_update
            + change_set.rules_to_remove
        )
        if len(touched) != 1:
            message = "one compliance browser run must touch exactly one managed rule"
            raise StaleConfirmation(message)
        await self._open_admin_url(str(self._settings.gmail_compliance_url))
        rule = touched[0]
        operation: Literal["create", "update", "remove", "set_enabled"]
        operation = (
            "create"
            if change_set.rules_to_create
            else "remove"
            if change_set.rules_to_remove
            else "update"
        )
        page = ContentCompliancePage(
            self._require_page(),
            self._navigator,
            candidate_limit=self._settings.browser_candidate_limit,
            max_steps=self._settings.browser_agent_max_steps,
        )
        compliance_permit = ComplianceBrowserPermit(
            approval_id=permit.approval_id,
            plan_hash=permit.plan_hash,
            before_state_hash=permit.before_state_hash,
            change_set_hash=permit.change_set_hash,
            target_rule_hash=canonical_hash(rule),
            target_ou=permit.target_ou,
            target_ownership_id=permit.target_ownership_id,
            operation=operation,
            approved=True,
        )
        result = (
            await page.remove_rule(rule, compliance_permit)
            if operation == "remove"
            else await page.apply_rule(rule, compliance_permit)
        )
        return AdminBrowserApplyResult(
            completed=result.completed,
            steps=result.steps,
            final_page_state=result.final_page_state,
        )

    async def _read_address_list(self, template: ManagedAddressList) -> ManagedAddressList:
        page = self._require_page()
        await self._open_admin_url(str(self._settings.gmail_settings_url))
        await self._run_goal(
            (
                f"Open the exact Gmail address list named {template.display_name!r} through the "
                "visible Create or edit list controls. Do not change it. Choose complete only "
                "when the list name and every entry are visible."
            ),
            inputs=(),
            mutation_allowed=False,
            target_ou="/",
            navigation_identity=template.display_name,
        )
        page = self._require_page()
        observed = await self._extractor.extract(
            page,
            ObservedAddressList,
            "Transcribe the open address list name and every address or domain entry.",
        )
        _require_expected_identity(observed.display_name, template.display_name, "address list")
        entries = tuple(_address_entry(value) for value in observed.entries)
        return template.model_copy(update={"entries": entries})

    async def _open_admin_url(self, url: str) -> None:
        page = self._require_page()
        await page.goto(url, wait_until="domcontentloaded")
        deadline = asyncio.get_running_loop().time() + self._settings.browser_login_timeout_seconds
        while urlparse(page.url).hostname != "admin.google.com":
            if asyncio.get_running_loop().time() >= deadline:
                message = "Google Admin login was not completed within the attended time limit"
                raise TimeoutError(message)
            await asyncio.sleep(1)
        await page.wait_for_load_state("domcontentloaded")
        identity = await self._extractor.extract(
            page,
            AdminIdentityObservation,
            "Read the signed-in administrator email and managed Workspace domain from the UI.",
        )
        if (
            identity.administrator_email.casefold()
            != self._settings.expected_admin_email.casefold()
        ):
            message = "signed-in Google administrator does not match the configured identity"
            raise StaleConfirmation(message)
        if (
            identity.workspace_domain.casefold()
            != self._settings.expected_workspace_domain.casefold()
        ):
            message = "Google Workspace domain does not match the configured tenant"
            raise StaleConfirmation(message)

    async def _run_goal(  # noqa: C901, PLR0913 - explicit safety gates.
        self,
        goal: str,
        *,
        inputs: tuple[BrowserInput, ...],
        mutation_allowed: bool,
        target_ou: str,
        commit_identity: str | None = None,
        commit_requires_ou: bool = False,
        navigation_identity: str | None = None,
    ) -> tuple[tuple[str, ...], bool]:
        page = self._require_page()
        history: list[str] = []
        previous_step = ""
        repeated = 0
        for _index in range(self._settings.browser_agent_max_steps):
            if urlparse(page.url).hostname != "admin.google.com":
                message = "browser left the approved Google Admin host"
                raise UnknownPageState(message)
            state = await self._detect_state()
            if state is AdminPageState.UNKNOWN:
                message = "Google Admin page identity was not established"
                raise UnknownPageState(message)
            snapshot = (await page.locator("body").aria_snapshot())[:30_000]
            catalog = await SemanticCatalog.capture(
                page,
                limit=self._settings.browser_candidate_limit,
            )
            step = await self._navigator.choose_step(
                goal,
                BrowserObservation(
                    page_state=state,
                    url=page.url,
                    aria_snapshot=snapshot,
                    candidates=catalog.candidates,
                    inputs=inputs,
                ),
                await page.screenshot(type="png"),
            )
            rendered = step.model_dump_json()
            history.append(rendered)
            repeated = repeated + 1 if rendered == previous_step else 1
            previous_step = rendered
            if repeated >= _REPEATED_STEP_LIMIT:
                message = "browser model repeated the same action three times"
                raise SelectorNotFound(message)
            if step.action == "complete":
                return tuple(history), True
            candidate = _candidate(catalog, step.candidate_id)
            if not mutation_allowed and _mutates(
                step.action,
                candidate,
                navigation_identity=navigation_identity,
            ):
                message = "read-only browser goal proposed a mutation"
                raise SelectorNotFound(message)
            if not mutation_allowed and candidate is not None and _is_commit(candidate):
                message = "read-only browser goal proposed a commit control"
                raise SelectorNotFound(message)
            requires_target_gate = (
                mutation_allowed
                and candidate is not None
                and (
                    _is_commit(candidate)
                    or (
                        candidate.role == "button"
                        and _mutates(
                            step.action,
                            candidate,
                            navigation_identity=commit_identity,
                        )
                    )
                )
            )
            if requires_target_gate:
                if commit_identity is not None:
                    _require_visible_identity(snapshot, commit_identity, "managed resource")
                if commit_requires_ou:
                    _require_target_ou(snapshot, target_ou)
            await execute_step(page, catalog, step, inputs)
            page = await self._adopt_latest_admin_page(page)
        return tuple(history), False

    async def _adopt_latest_admin_page(self, current: Page) -> Page:
        """Follow Google address-list editors that intentionally open in a new tab."""

        admin_pages = [
            candidate
            for candidate in current.context.pages
            if urlparse(candidate.url).hostname == "admin.google.com"
        ]
        if not admin_pages:
            return current
        latest = admin_pages[-1]
        if latest is not current:
            await latest.wait_for_load_state("domcontentloaded")
            self._page = latest
        return latest

    async def _detect_state(self) -> AdminPageState:
        page = self._require_page()
        dialog = page.get_by_role(
            "dialog",
            name=re.compile(r"(add|edit|configure).*(setting|rule|list)", re.IGNORECASE),
        )
        if await dialog.count() == 1:
            name = ((await dialog.get_attribute("aria-label")) or "").casefold()
            if "list" in name:
                return AdminPageState.ADDRESS_LIST_EDITOR
            if "compliance" in page.url.casefold():
                return AdminPageState.CONTENT_COMPLIANCE_RULE_EDITOR
            return AdminPageState.BLOCKED_SENDER_RULE_EDITOR
        if "compliance" in page.url.casefold():
            return await ContentCompliancePage(
                page,
                self._navigator,
                candidate_limit=self._settings.browser_candidate_limit,
                max_steps=self._settings.browser_agent_max_steps,
            ).detect_state()
        return await GmailSpamSettingsPage(page).detect_state()

    def _require_page(self) -> Page:
        if self._page is None:
            message = "attended browser session is not open"
            raise RuntimeError(message)
        return self._page


def _require_expected_identity(observed: str, expected: str, label: str) -> None:
    if " ".join(observed.split()).casefold() != " ".join(expected.split()).casefold():
        message = f"observed {label} does not match the managed identity"
        raise StaleConfirmation(message)


def _restore_local_expression_metadata(
    observed: tuple[ObservedComplianceExpression, ...],
    expected: tuple[ComplianceExpression, ...],
) -> tuple[ComplianceExpression, ...]:
    """Reattach capability slugs that are local gates, not Google-persisted fields."""

    if len(observed) != len(expected):
        message = "observed expression count does not match the managed compliance rule"
        raise StaleConfirmation(message)
    restored: list[ComplianceExpression] = []
    for index, expression in enumerate(observed):
        if isinstance(expression, ObservedPredefinedContentMatch):
            template = expected[index]
            if not isinstance(template, PredefinedContentMatch):
                message = "observed predefined expression does not match managed expression type"
                raise StaleConfirmation(message)
            restored.append(
                PredefinedContentMatch(
                    **expression.model_dump(),
                    required_edition_capability=template.required_edition_capability,
                )
            )
        else:
            restored.append(expression)
    return tuple(restored)


def _address_entry(value: str) -> AddressEntry:
    normalized = value.strip()
    return AddressEntry(kind="email" if "@" in normalized else "domain", value=normalized)


def _candidate(catalog: SemanticCatalog, candidate_id: str | None) -> BrowserCandidate | None:
    if candidate_id is None:
        return None
    return next((item for item in catalog.candidates if item.candidate_id == candidate_id), None)


def _mutates(
    action: str,
    candidate: BrowserCandidate | None,
    *,
    navigation_identity: str | None = None,
) -> bool:
    if action in {"fill", "check", "uncheck", "select"}:
        return True
    if action != "click" or candidate is None:
        return action == "click"
    if candidate.role == "link":
        return _is_commit(candidate) or not (
            _is_navigation_control(candidate)
            or _matches_navigation_identity(candidate, navigation_identity)
        )
    if candidate.role != "button":
        return True
    return not _is_navigation_control(candidate) and not _matches_navigation_identity(
        candidate,
        navigation_identity,
    )


def _is_navigation_control(candidate: BrowserCandidate) -> bool:
    return bool(
        re.fullmatch(
            r"(open|view|edit|show|expand|collapse|details|back|cancel|close|add|configure)"
            r"(?:\s+.*)?",
            candidate.accessible_name.strip(),
            re.IGNORECASE,
        )
    )


def _matches_navigation_identity(
    candidate: BrowserCandidate,
    expected: str | None,
) -> bool:
    return expected is not None and " ".join(candidate.accessible_name.split()).casefold() == (
        " ".join(expected.split()).casefold()
    )


def _is_commit(candidate: BrowserCandidate) -> bool:
    return bool(
        re.fullmatch(
            r"(save|add setting|update|apply|delete|remove|confirm|enable|disable)(?:\s+.*)?",
            candidate.accessible_name.strip(),
            re.IGNORECASE,
        )
    )


def _require_target_ou(snapshot: str, target_ou: str) -> None:
    normalized = " ".join(snapshot.split()).casefold()
    expected = "root organizational unit" if target_ou == "/" else target_ou
    if expected.casefold() not in normalized and target_ou.casefold() not in normalized:
        message = f"approved organizational unit is not visible before save: {target_ou}"
        raise RootOuNotConfirmed(message)


def _require_visible_identity(snapshot: str, expected: str, label: str) -> None:
    normalized = " ".join(snapshot.split()).casefold()
    if " ".join(expected.split()).casefold() not in normalized:
        message = f"approved {label} is not visible before save: {expected}"
        raise StaleConfirmation(message)


def _validate_admin_permit(
    change_set: ChangeSet | ComplianceChangeSet,
    permit: AdminBrowserPermit,
    *,
    surface: Literal["blocked_senders", "content_compliance"],
) -> None:
    if (
        not permit.approved
        or permit.surface != surface
        or permit.change_set_hash != canonical_hash(change_set)
    ):
        message = "approved browser permit no longer matches the exact change set"
        raise StaleConfirmation(message)
    if permit.before_state_hash != canonical_hash(change_set.before_state):
        message = "approved browser permit no longer matches the before-state"
        raise StaleConfirmation(message)
    before_rules = change_set.before_state.rules
    after_rules = change_set.expected_after.rules
    policy_versions = tuple(
        rule
        for rule in (*before_rules, *after_rules)
        if rule.ownership_id == permit.target_ownership_id
    )
    if not policy_versions:
        message = "approved managed policy identity is absent from the bound state transition"
        raise StaleConfirmation(message)
    target_paths = {
        rule.target_ou.path
        if isinstance(rule, ManagedContentComplianceRule)
        else rule.target_ou
        for rule in policy_versions
    }
    if target_paths != {permit.target_ou}:
        message = "approved organizational unit no longer matches the managed policy"
        raise StaleConfirmation(message)


def _address_list_inputs(
    operation: str,
    address_list: ManagedAddressList,
) -> tuple[BrowserInput, ...]:
    values = [(f"{operation} address list name", address_list.display_name)]
    values.extend(
        (f"Complete address-list entry {index}", entry.value)
        for index, entry in enumerate(address_list.entries, start=1)
    )
    return _browser_inputs(values)


def _blocked_rule_inputs(
    operation: str,
    rule: ManagedBlockedSenderRule,
) -> tuple[BrowserInput, ...]:
    values = [
        (f"{operation} blocked-sender rule name", rule.display_name),
        ("Organizational unit", rule.target_ou),
        ("Enabled state", str(rule.enabled).lower()),
    ]
    values.extend(
        (f"Blocked address list {index}", name)
        for index, name in enumerate(rule.address_list_names, start=1)
    )
    values.extend(
        (f"Bypass address list {index}", name)
        for index, name in enumerate(rule.bypass_address_list_names, start=1)
    )
    if rule.rejection_notice is not None:
        values.append(("Custom rejection notice", rule.rejection_notice))
    return _browser_inputs(values)


def _browser_inputs(values: list[tuple[str, str]]) -> tuple[BrowserInput, ...]:
    return tuple(
        BrowserInput(input_id=f"i{index:03d}", label=label, value=value)
        for index, (label, value) in enumerate(values)
    )
