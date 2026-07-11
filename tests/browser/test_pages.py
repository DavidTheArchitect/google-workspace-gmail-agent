"""Read-only page identity and explicit live-UI gates."""

import pytest

from compliance_agent.browser.pages.address_lists import AddressListsPage
from compliance_agent.browser.pages.gmail_spam_settings import GmailSpamSettingsPage
from compliance_agent.browser.pages.login import detect_authentication_state
from compliance_agent.browser.states import AdminPageState
from compliance_agent.exceptions import UnknownPageState, UnvalidatedUiContract


class CountLocator:
    """Return one controlled semantic-heading count."""

    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class HeadingPage:
    """Resolve heading counts by their reviewed accessible-name pattern."""

    def __init__(self, *, spam_count: int, blocked_count: int) -> None:
        self._spam_count = spam_count
        self._blocked_count = blocked_count

    def get_by_role(self, _role: str, *, name) -> CountLocator:
        count = self._blocked_count if "Blocked" in name.pattern else self._spam_count
        return CountLocator(count)


class UrlPage:
    """Expose only the URL used by authentication-state detection."""

    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spam_count", "blocked_count", "expected"),
    [
        (0, 0, AdminPageState.UNKNOWN),
        (1, 0, AdminPageState.GMAIL_SPAM_SETTINGS),
        (1, 1, AdminPageState.BLOCKED_SENDERS_SECTION),
    ],
)
async def test_gmail_page_state_requires_unique_semantic_headings(
    spam_count: int,
    blocked_count: int,
    expected: AdminPageState,
) -> None:
    page = GmailSpamSettingsPage(
        HeadingPage(spam_count=spam_count, blocked_count=blocked_count)  # type: ignore[arg-type]
    )

    assert await page.detect_state() == expected


@pytest.mark.asyncio
async def test_page_objects_fail_closed_before_live_fixture_acceptance() -> None:
    page = GmailSpamSettingsPage(
        HeadingPage(spam_count=1, blocked_count=0)  # type: ignore[arg-type]
    )

    with pytest.raises(UnknownPageState):
        await page.require_blocked_senders_state()
    with pytest.raises(UnvalidatedUiContract):
        await page.read_state()
    with pytest.raises(UnvalidatedUiContract):
        await AddressListsPage().read_lists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://accounts.google.com/signin", AdminPageState.LOGIN_REQUIRED),
        ("https://accounts.google.com/accountchooser", AdminPageState.ACCOUNT_CHOOSER),
        ("https://accounts.google.com/signin/challenge/pwd", AdminPageState.TWO_STEP_VERIFICATION),
        ("https://admin.google.com/ac/apps/gmail", AdminPageState.UNKNOWN),
    ],
)
async def test_authentication_state_uses_only_known_google_account_routes(
    url: str,
    expected: AdminPageState,
) -> None:
    assert await detect_authentication_state(UrlPage(url)) == expected  # type: ignore[arg-type]
