"""Browser session cleanup behavior without launching a browser."""

from pathlib import Path

import pytest

from compliance_agent.browser.session import BrowserSession
from compliance_agent.settings import Settings


class FailingContext:
    """Raise during close to prove later cleanup still runs."""

    async def close(self) -> None:
        message = "context close failed"
        raise RuntimeError(message)


class RecordingPlaywright:
    """Record Playwright shutdown."""

    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class RecordingLock:
    """Record release even when browser cleanup fails."""

    def __init__(self) -> None:
        self.acquired = False
        self.released = False

    def acquire(self) -> None:
        self.acquired = True

    def release(self) -> None:
        self.released = True


class WorkingContext:
    """Minimal persistent-context lifecycle double."""

    def __init__(self) -> None:
        self.pages = [object()]
        self.closed = False
        self.navigation_timeout_ms: int | None = None
        self.action_timeout_ms: int | None = None

    def set_default_navigation_timeout(self, timeout_ms: int) -> None:
        self.navigation_timeout_ms = timeout_ms

    def set_default_timeout(self, timeout_ms: int) -> None:
        self.action_timeout_ms = timeout_ms

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    """Return a controlled persistent browser context."""

    def __init__(self, context: WorkingContext) -> None:
        self._context = context
        self.launch_options: dict[str, object] = {}

    async def launch_persistent_context(self, **kwargs):
        self.launch_options = kwargs
        return self._context


class WorkingPlaywright(RecordingPlaywright):
    """Expose the Chromium launcher used by the session."""

    def __init__(self, context: WorkingContext) -> None:
        super().__init__()
        self.chromium = FakeChromium(context)


class PlaywrightStarter:
    """Async starter returned by the Playwright factory."""

    def __init__(self, playwright: WorkingPlaywright) -> None:
        self._playwright = playwright

    async def start(self) -> WorkingPlaywright:
        return self._playwright


@pytest.mark.asyncio
async def test_session_releases_playwright_and_lock_when_context_close_fails(
    tmp_path: Path,
) -> None:
    lock = RecordingLock()
    session = BrowserSession(
        Settings(
            profile_dir=tmp_path / "profile",
            audit_dir=tmp_path / "audit",
            state_dir=tmp_path / "state",
        ),
        lock,  # type: ignore[arg-type] - protocol-shaped cleanup double.
    )
    playwright = RecordingPlaywright()
    session._context = FailingContext()  # type: ignore[assignment] - lifecycle failure double.
    session._playwright = playwright  # type: ignore[assignment] - lifecycle failure double.

    with pytest.raises(RuntimeError, match="context close failed"):
        await session.__aexit__(None, None, None)

    assert playwright.stopped
    assert lock.released


@pytest.mark.asyncio
async def test_session_starts_configures_and_closes_persistent_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = WorkingContext()
    playwright = WorkingPlaywright(context)
    lock = RecordingLock()
    monkeypatch.setattr(
        "compliance_agent.browser.session.async_playwright",
        lambda: PlaywrightStarter(playwright),
    )
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
    )

    async with BrowserSession(settings, lock) as session:  # type: ignore[arg-type]
        assert session.page is context.pages[0]
        assert context.navigation_timeout_ms == settings.navigation_timeout_ms
        assert context.action_timeout_ms == settings.action_timeout_ms
        assert playwright.chromium.launch_options["chromium_sandbox"] is True

    assert lock.acquired
    assert lock.released
    assert context.closed
    assert playwright.stopped
