"""Reflex development and production configuration."""

import hashlib
import os
from pathlib import Path

from compliance_agent.startup import console_public_origin


def _external_web_directory() -> Path:
    """Keep generated Node trees out of OneDrive-backed repositories on Windows."""

    repository = Path.cwd().resolve()
    repository_key = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()[:12]
    configured = os.environ.get("GMAIL_AGENT_REFLEX_WEB_DIR")
    root = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".compliance_agent" / "reflex-web"
    )
    target = (root / repository_key).resolve()
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


if "REFLEX_WEB_WORKDIR" not in os.environ:
    os.environ["REFLEX_WEB_WORKDIR"] = str(_external_web_directory())

import reflex as rx  # noqa: E402 - Reflex reads the workdir environment during import.

_FRONTEND_PORT = int(os.environ.get("GMAIL_AGENT_CONSOLE_PORT", "8765"))
_BACKEND_PORT = int(os.environ.get("GMAIL_AGENT_CONSOLE_BACKEND_PORT", str(_FRONTEND_PORT)))
_PUBLIC_URL = os.environ.get("GMAIL_AGENT_PUBLIC_URL")
_BACKEND_HOST = os.environ.get(
    "GMAIL_AGENT_CONSOLE_HOST",
    os.environ.get("CA_CONSOLE_BIND_HOST", "127.0.0.1"),
)

config = rx.Config(
    app_name="gmail_admin_agent",
    frontend_port=_FRONTEND_PORT,
    backend_port=_BACKEND_PORT,
    backend_host=_BACKEND_HOST,
    api_url=_PUBLIC_URL or console_public_origin(_BACKEND_PORT),
    deploy_url=_PUBLIC_URL or console_public_origin(_FRONTEND_PORT),
    telemetry_enabled=False,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(appearance="inherit", accent_color="cyan", radius="medium")
        ),
    ],
)
