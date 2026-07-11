"""Open the dedicated headed profile for attended Google authentication."""

import asyncio

from compliance_agent.browser.session import BrowserSession
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.settings import Settings
from compliance_agent.version import __version__


async def login() -> None:
    """Let the operator authenticate directly in Chrome without credential capture."""

    settings = Settings(headless=False, dry_run=True, plan_only=False)
    lock = ProcessLock(
        settings.state_dir / "run.lock",
        run_id="manual-login",
        started_at=SystemClock().now(),
        application_version=__version__,
    )
    async with BrowserSession(settings, lock) as session:
        await session.page.goto(str(settings.google_admin_base_url))
        await asyncio.to_thread(
            input,
            "Complete sign-in in Chrome, then press Enter here to close: ",
        )


if __name__ == "__main__":
    asyncio.run(login())
