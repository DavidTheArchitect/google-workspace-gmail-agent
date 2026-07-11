"""Gmail spam-settings page identity gate.

Read and mutation locators are intentionally absent until sanitized current-UI fixtures pass the
supervised acceptance procedure. Shipping guessed write selectors would violate the safety model.
"""

import re

from playwright.async_api import Page

from compliance_agent.browser.accessible_names import BLOCKED_SENDERS_HEADING, SPAM_SETTINGS_HEADING
from compliance_agent.browser.states import AdminPageState
from compliance_agent.exceptions import UnknownPageState, UnvalidatedUiContract


class GmailSpamSettingsPage:
    """Establish documented page identity without exposing mutation methods."""

    def __init__(self, page: Page) -> None:
        self._page = page

    async def detect_state(self) -> AdminPageState:
        """Return a known read-only page state only when semantic headings are unique."""

        spam_heading = self._page.get_by_role(
            "heading",
            name=re.compile(SPAM_SETTINGS_HEADING, re.IGNORECASE),
        )
        if await spam_heading.count() != 1:
            return AdminPageState.UNKNOWN
        blocked_heading = self._page.get_by_role(
            "heading",
            name=re.compile(BLOCKED_SENDERS_HEADING, re.IGNORECASE),
        )
        if await blocked_heading.count() == 1:
            return AdminPageState.BLOCKED_SENDERS_SECTION
        return AdminPageState.GMAIL_SPAM_SETTINGS

    async def require_blocked_senders_state(self) -> None:
        """Fail closed unless the documented section is uniquely identified."""

        if await self.detect_state() != AdminPageState.BLOCKED_SENDERS_SECTION:
            message = "blocked-senders page identity was not established"
            raise UnknownPageState(message)

    async def read_state(self) -> None:
        """Gate parser work on sanitized fixtures instead of guessing current DOM structure."""

        message = "blocked-sender parsing requires supervised sanitized UI fixtures"
        raise UnvalidatedUiContract(message)
