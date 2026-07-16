"""Application boundary for approved Google Admin content-compliance browser actions."""

from collections.abc import Callable

from compliance_agent.browser.pages.content_compliance import (
    ComplianceBrowserPermit,
    ComplianceBrowserRunResult,
    ContentCompliancePage,
)
from compliance_agent.exceptions import StaleConfirmation
from compliance_agent.schemas.compliance import ManagedContentComplianceRule


class ComplianceBrowserActionService:
    """Dispatch one permit to exactly one supported Playwright page operation."""

    def __init__(self, page_factory: Callable[[], ContentCompliancePage]) -> None:
        self._page_factory = page_factory
        self._consumed_approval_ids: set[str] = set()

    async def execute(
        self,
        rule: ManagedContentComplianceRule,
        permit: ComplianceBrowserPermit,
    ) -> ComplianceBrowserRunResult:
        """Execute the operation encoded in the permit; callers cannot override it."""

        if permit.approval_id in self._consumed_approval_ids:
            message = "content-compliance browser permit was already consumed"
            raise StaleConfirmation(message)
        self._consumed_approval_ids.add(permit.approval_id)
        page = self._page_factory()
        if permit.operation in {"create", "update"}:
            return await page.apply_rule(rule, permit)
        if permit.operation == "remove":
            return await page.remove_rule(rule, permit)
        return await page.set_rule_enabled(rule, enabled=rule.enabled, permit=permit)
