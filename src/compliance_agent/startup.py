"""Human-readable startup diagnostics and loopback port selection."""

from __future__ import annotations

import os
import platform
import re
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from compliance_agent.settings import Settings

_PORT_SEARCH_SPAN = 20
_MAX_DNS_NAME_LENGTH = 253
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


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


def console_public_origin(
    port: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the exact local or private Codespaces origin for one console port."""

    values = os.environ if environ is None else environ
    if values.get("CODESPACES", "").lower() != "true":
        return f"http://127.0.0.1:{port}"
    codespace_name = values.get("CODESPACE_NAME", "").lower()
    forwarding_domain = values.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "").lower()
    forwarded_label = f"{codespace_name}-{port}"
    if not _valid_dns_label(forwarded_label) or not _valid_dns_name(forwarding_domain):
        message = "Codespaces forwarding environment is missing or invalid"
        raise ValueError(message)
    return f"https://{forwarded_label}.{forwarding_domain}"


def _valid_dns_name(value: str) -> bool:
    return bool(
        value
        and len(value) <= _MAX_DNS_NAME_LENGTH
        and all(_valid_dns_label(part) for part in value.split("."))
    )


def _valid_dns_label(value: str) -> bool:
    return bool(_DNS_LABEL.fullmatch(value))


def ollama_available(settings: Settings) -> bool:
    """Probe the configured Ollama service socket without sending data."""

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
        StartupCheck("pass", "Ollama", "The configured model service is reachable.")
        if ollama_probe(settings)
        else StartupCheck(
            "warn",
            "Ollama",
            "The configured model service is not reachable. The deterministic form still works.",
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
