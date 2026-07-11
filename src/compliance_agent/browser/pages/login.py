"""Authentication-state detection without credential-field access."""

from urllib.parse import urlparse

from playwright.async_api import Page

from compliance_agent.browser.routes import GOOGLE_ACCOUNTS_HOST
from compliance_agent.browser.states import AdminPageState


async def detect_authentication_state(page: Page) -> AdminPageState:
    """Classify only URL-level login states; never inspect password or 2SV values."""

    parsed = urlparse(page.url)
    if parsed.hostname == GOOGLE_ACCOUNTS_HOST:
        if "challenge" in parsed.path:
            return AdminPageState.TWO_STEP_VERIFICATION
        if "accountchooser" in parsed.path:
            return AdminPageState.ACCOUNT_CHOOSER
        return AdminPageState.LOGIN_REQUIRED
    # A recognized Admin host is still insufficient to claim a specific Gmail settings page.
    return AdminPageState.UNKNOWN
