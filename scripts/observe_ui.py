"""Attended read-only page-state observation; never activates mutation controls."""

import argparse
import asyncio
import json
from pathlib import Path

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


async def observe(output_directory: Path | None) -> None:
    """Open the documented settings entry point and optionally save sanitized evidence."""

    settings = Settings(headless=False, dry_run=True, plan_only=False)
    lock = ProcessLock(
        settings.state_dir / "run.lock",
        run_id="read-only-observation",
        started_at=SystemClock().now(),
        application_version=__version__,
    )
    async with BrowserSession(settings, lock) as session:
        await session.page.goto(str(settings.gmail_settings_url))
        authentication_state = await detect_authentication_state(session.page)
        state = (
            authentication_state
            if authentication_state != AdminPageState.UNKNOWN
            else await GmailSpamSettingsPage(session.page).detect_state()
        )
        metadata = {
            "url": sanitize_url(session.page.url),
            "title": redact_text(await session.page.title()),
            "detected_state": state.value,
        }
        print(json.dumps(metadata, indent=2, sort_keys=True))
        if output_directory is not None:
            if state in {
                AdminPageState.LOGIN_REQUIRED,
                AdminPageState.ACCOUNT_CHOOSER,
                AdminPageState.TWO_STEP_VERIFICATION,
            }:
                message = "authentication-page evidence capture is prohibited"
                raise RuntimeError(message)
            html = sanitize_html(await session.page.content())
            aria = redact_text(await session.page.locator("body").aria_snapshot())
            await asyncio.to_thread(_write_evidence, output_directory, metadata, html, aria)
        await asyncio.to_thread(input, "Inspect the read-only page, then press Enter to close: ")


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
