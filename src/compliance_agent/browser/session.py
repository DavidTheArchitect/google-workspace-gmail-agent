"""Dedicated persistent headed-Chrome session lifecycle."""

from contextlib import suppress
from types import TracebackType

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.settings import Settings


class BrowserSession:
    """Launch a dedicated persistent profile only while the run lock is held."""

    def __init__(self, settings: Settings, run_lock: ProcessLock) -> None:
        self._settings = settings
        self._run_lock = run_lock
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "BrowserSession":
        self._run_lock.acquire()
        try:
            self._playwright = await async_playwright().start()
            self._settings.profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._settings.profile_dir.chmod(0o700)
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._settings.profile_dir,
                headless=self._settings.headless,
                channel="chrome",
                chromium_sandbox=True,
            )
            self._context.set_default_navigation_timeout(self._settings.navigation_timeout_ms)
            self._context.set_default_timeout(self._settings.action_timeout_ms)
            started_session = self
        except BaseException:
            with suppress(BaseException):
                await self._stop()
            with suppress(BaseException):
                self._run_lock.release()
            raise
        else:
            return started_session

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        try:
            await self._stop()
        finally:
            self._run_lock.release()

    @property
    def page(self) -> Page:
        """Return one application page after the context has started."""

        if self._context is None:
            message = "browser session has not started"
            raise RuntimeError(message)
        if self._context.pages:
            return self._context.pages[0]
        message = "persistent browser context did not create a page"
        raise RuntimeError(message)

    async def _stop(self) -> None:
        context = self._context
        playwright = self._playwright
        self._context = None
        self._playwright = None
        try:
            if context is not None:
                await context.close()
        finally:
            if playwright is not None:
                await playwright.stop()
