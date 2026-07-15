"""Attended read-only page-state observation; never activates mutation controls."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page

from compliance_agent.audit.redaction import redact_text
from compliance_agent.browser.diagnostics import sanitize_html, sanitize_url
from compliance_agent.browser.pages.gmail_spam_settings import GmailSpamSettingsPage
from compliance_agent.browser.pages.login import detect_authentication_state
from compliance_agent.browser.session import BrowserSession
from compliance_agent.browser.states import AdminPageState
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.settings import Settings
from compliance_agent.version import __version__

_AUTHENTICATION_STATES = frozenset(
    {
        AdminPageState.LOGIN_REQUIRED,
        AdminPageState.ACCOUNT_CHOOSER,
        AdminPageState.TWO_STEP_VERIFICATION,
    }
)
_ADMIN_NAVIGATION_PATH = "Menu > Apps > Google Workspace > Gmail > Spam, Phishing and Malware"
_GOOGLE_HELP_URL = (
    "https://knowledge.workspace.google.com/admin/gmail/advanced/"
    "block-messages-from-an-email-address-or-domain"
)


async def observe(output_directory: Path | None) -> None:
    """Open the documented settings entry point and optionally save sanitized evidence."""

    _require_new_output_directory(output_directory)
    settings = Settings(headless=False, dry_run=True, plan_only=False)
    lock = ProcessLock(
        settings.state_dir / "run.lock",
        run_id="read-only-observation",
        started_at=SystemClock().now(),
        application_version=__version__,
    )
    async with BrowserSession(settings, lock) as session:
        page = session.page
        await page.goto(str(settings.google_admin_base_url))
        await _complete_sign_in(page)
        await page.goto(str(settings.gmail_settings_url))
        if _is_not_found_title(await page.title()):
            print(
                "The direct Gmail settings link was unavailable. Opening the Admin console "
                "home so you can use Google's menu instead."
            )
            await page.goto(str(settings.google_admin_base_url))
        print(f"In Google Admin, open: {_ADMIN_NAVIGATION_PATH}")
        print("Required access: a Google Workspace administrator with Gmail Settings privilege.")
        print(f"Google's current instructions: {_GOOGLE_HELP_URL}")
        await _pause(
            "Sign in if prompted. When the Spam, Phishing and Malware page is visible, "
            "press Enter here to inspect it: "
        )
        state = await _detect_state(page)
        title = redact_text(await page.title())
        _require_observation_target(page.url, title, state)
        metadata = {
            "url": sanitize_url(page.url),
            "title": title,
            "detected_state": state.value,
        }
        print(json.dumps(metadata, indent=2, sort_keys=True))
        if output_directory is not None:
            html = sanitize_html(await page.content())
            aria = redact_text(await page.locator("body").aria_snapshot())
            await asyncio.to_thread(_write_evidence, output_directory, metadata, html, aria)
            print(f"Sanitized evidence saved to {output_directory}.")
        await _pause("Inspect the read-only page, then press Enter to close: ")


async def _complete_sign_in(page: Page) -> None:
    state = await detect_authentication_state(page)
    if state not in _AUTHENTICATION_STATES:
        return
    print("Google sign-in is open. Credentials and authentication pages are never captured.")
    await _pause("Complete sign-in in Chrome, then press Enter here to continue: ")
    if await detect_authentication_state(page) in _AUTHENTICATION_STATES:
        message = "Google sign-in is not complete; no evidence was captured"
        raise RuntimeError(message)


async def _detect_state(page: Page) -> AdminPageState:
    authentication_state = await detect_authentication_state(page)
    if authentication_state != AdminPageState.UNKNOWN:
        return authentication_state
    return await GmailSpamSettingsPage(page).detect_state()


async def _pause(prompt: str) -> None:
    await asyncio.to_thread(input, prompt)


def _is_not_found_title(title: str) -> bool:
    normalized = title.casefold()
    return "404" in normalized and "not found" in normalized


def _looks_like_gmail_spam_settings(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.casefold().rstrip("/")
    return parsed.hostname == "admin.google.com" and path.endswith("/apps/gmail/spam")


def _require_observation_target(url: str, title: str, state: AdminPageState) -> None:
    if state in _AUTHENTICATION_STATES:
        message = "Google sign-in is not complete; authentication-page evidence was not captured"
        raise RuntimeError(message)
    if _is_not_found_title(title):
        message = (
            "Google returned a 404. Confirm that the signed-in account belongs to Google "
            "Workspace, has Gmail Settings administrator privilege, and uses an edition with "
            "advanced Gmail settings; no evidence was captured"
        )
        raise RuntimeError(message)
    if state == AdminPageState.UNKNOWN and not _looks_like_gmail_spam_settings(url):
        safe_url = sanitize_url(url)
        message = (
            f"The browser is not on Gmail's Spam, Phishing and Malware page ({safe_url}). "
            f"Open {_ADMIN_NAVIGATION_PATH} and run the observation again; no evidence was captured"
        )
        raise RuntimeError(message)


def _require_new_output_directory(output_directory: Path | None) -> None:
    if output_directory is not None and output_directory.exists():
        message = (
            f"output directory already exists: {output_directory}. Choose a new empty path so "
            "earlier evidence is never overwritten"
        )
        raise FileExistsError(message)


def _write_evidence(
    output_directory: Path,
    metadata: dict[str, str],
    html: str,
    aria: str,
) -> None:
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    output_directory.chmod(0o700)
    _write_protected_text(
        output_directory / "metadata.json",
        json.dumps(metadata, indent=2, sort_keys=True),
    )
    _write_protected_text(output_directory / "page.html", html)
    _write_protected_text(output_directory / "aria.txt", aria)


def _write_protected_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    """Parse the optional protected evidence directory and start observation."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    asyncio.run(observe(args.output_directory))


if __name__ == "__main__":
    main()
