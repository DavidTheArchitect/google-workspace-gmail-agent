"""Human-readable startup diagnostics and loopback port selection."""

from __future__ import annotations

import platform
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from compliance_agent.settings import Settings

_PORT_SEARCH_SPAN = 20


@dataclass(frozen=True, slots=True)
class StartupCheck:
    """One concise startup diagnostic."""

    level: Literal["pass", "warn"]
    name: str
    detail: str


def port_available(port: int) -> bool:
    """Return whether a loopback listener can bind to the requested port."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def choose_console_port(
    preferred: int,
    *,
    available: Callable[[int], bool] = port_available,
) -> int:
    """Choose the preferred port or the next available user-space port."""

    last = min(preferred + _PORT_SEARCH_SPAN, 65_535)
    for candidate in range(preferred, last + 1):
        if available(candidate):
            return candidate
    message = f"no free console port found from {preferred} through {last}"
    raise OSError(message)


def ollama_available(settings: Settings) -> bool:
    """Probe the configured loopback Ollama socket without sending data."""

    host = settings.ollama_base_url.host
    port = settings.ollama_base_url.port or 80
    if host is None:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def collect_startup_checks(
    settings: Settings,
    *,
    available: Callable[[int], bool] = port_available,
    ollama_probe: Callable[[Settings], bool] = ollama_available,
) -> tuple[StartupCheck, ...]:
    """Collect non-mutating checks for a friendly startup report."""

    selected_port = choose_console_port(settings.console_port, available=available)
    port_check = (
        StartupCheck("pass", "Console port", f"127.0.0.1:{selected_port} is available.")
        if selected_port == settings.console_port
        else StartupCheck(
            "warn",
            "Console port",
            f"{settings.console_port} is busy; startup will use {selected_port} automatically.",
        )
    )
    ollama_check = (
        StartupCheck("pass", "Ollama", "Local natural-language planning is available.")
        if ollama_probe(settings)
        else StartupCheck(
            "warn",
            "Ollama",
            "Not detected. The primary deterministic form will still work.",
        )
    )
    browser_detail = (
        "The secure console will open automatically."
        if settings.console_open_browser
        else "Automatic browser opening is disabled by CA_CONSOLE_OPEN_BROWSER."
    )
    return (
        StartupCheck(
            "pass",
            "Configuration",
            f"Validated in {settings.run_mode.value.replace('_', ' ')} mode.",
        ),
        StartupCheck("pass", "Python", platform.python_version()),
        StartupCheck(
            "pass",
            "Storage",
            "Profile, audit, and state paths are absolute, distinct, and non-overlapping.",
        ),
        port_check,
        ollama_check,
        StartupCheck(
            "pass" if settings.console_open_browser else "warn",
            "Browser",
            browser_detail,
        ),
    )


def format_startup_checks(checks: tuple[StartupCheck, ...]) -> str:
    """Format checks for terminals without requiring color support."""

    lines = ["Gmail Compliance Agent startup check"]
    lines.extend(f"[{check.level.upper()}] {check.name}: {check.detail}" for check in checks)
    lines.append("Ready to start. Warnings above are optional features or automatic fallbacks.")
    return "\n".join(lines)
