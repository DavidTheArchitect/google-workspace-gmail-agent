"""Regression coverage for the attended Google Admin observation helper."""

from pathlib import Path

import pytest

from compliance_agent.browser.states import AdminPageState
from scripts import observe_ui
from scripts.observe_ui import (
    _is_not_found_title,
    _looks_like_gmail_spam_settings,
    _require_new_output_directory,
    _require_observation_target,
)


class MutableUrlPage:
    """Expose the changing URL used by URL-only authentication detection."""

    def __init__(self, url: str) -> None:
        self.url = url


@pytest.mark.asyncio
async def test_observer_waits_for_attended_sign_in_before_continuing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MutableUrlPage("https://accounts.google.com/signin")
    prompts: list[str] = []

    async def complete_sign_in(prompt: str) -> None:
        prompts.append(prompt)
        page.url = "https://admin.google.com/"

    monkeypatch.setattr(observe_ui, "_pause", complete_sign_in)

    await observe_ui._complete_sign_in(page)  # type: ignore[arg-type]

    assert prompts == ["Complete sign-in in Chrome, then press Enter here to continue: "]


@pytest.mark.asyncio
async def test_observer_refuses_to_continue_while_sign_in_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MutableUrlPage("https://accounts.google.com/signin")

    async def remain_on_sign_in(_prompt: str) -> None:
        return None

    monkeypatch.setattr(observe_ui, "_pause", remain_on_sign_in)

    with pytest.raises(RuntimeError, match="sign-in is not complete"):
        await observe_ui._complete_sign_in(page)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Error 404 (Not Found)!!1", True),
        ("Google Admin", False),
        ("Spam, Phishing and Malware", False),
    ],
)
def test_observer_recognizes_google_not_found_titles(title: str, expected: bool) -> None:
    assert _is_not_found_title(title) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://admin.google.com/ac/apps/gmail/spam", True),
        ("https://admin.google.com/ac/apps/gmail/spam/", True),
        ("https://admin.google.com/ac/apps/gmail", False),
        ("https://admin.google.com/", False),
        ("https://example.com/ac/apps/gmail/spam", False),
    ],
)
def test_observer_accepts_only_the_documented_gmail_spam_route(
    url: str,
    expected: bool,
) -> None:
    assert _looks_like_gmail_spam_settings(url) is expected


def test_observer_refuses_authentication_404_and_wrong_page_evidence() -> None:
    with pytest.raises(RuntimeError, match="sign-in is not complete"):
        _require_observation_target(
            "https://accounts.google.com/signin",
            "Sign in",
            AdminPageState.LOGIN_REQUIRED,
        )
    with pytest.raises(RuntimeError, match="Google returned a 404"):
        _require_observation_target(
            "https://admin.google.com/ac/apps/gmail/spam",
            "Error 404 (Not Found)!!1",
            AdminPageState.UNKNOWN,
        )
    with pytest.raises(RuntimeError, match="browser is not on Gmail"):
        _require_observation_target(
            "https://admin.google.com/",
            "Google Admin",
            AdminPageState.UNKNOWN,
        )


def test_observer_requires_a_new_evidence_directory(tmp_path: Path) -> None:
    new_path = tmp_path / "new-evidence"
    _require_new_output_directory(new_path)

    existing_path = tmp_path / "existing-evidence"
    existing_path.mkdir()
    with pytest.raises(FileExistsError, match="never overwritten"):
        _require_new_output_directory(existing_path)
