"""Reflex development and production configuration."""

import hashlib
import os
from pathlib import Path

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


os.environ.setdefault("REFLEX_WEB_WORKDIR", str(_external_web_directory()))

import reflex as rx  # noqa: E402 - Reflex reads the workdir environment during import.

_FRONTEND_PORT = int(os.environ.get("GMAIL_AGENT_CONSOLE_PORT", "8765"))
_BACKEND_PORT = int(os.environ.get("GMAIL_AGENT_CONSOLE_BACKEND_PORT", str(_FRONTEND_PORT)))

config = rx.Config(
    app_name="gmail_admin_agent",
    frontend_port=_FRONTEND_PORT,
    backend_port=_BACKEND_PORT,
    backend_host="127.0.0.1",
    api_url=f"http://127.0.0.1:{_BACKEND_PORT}",
    deploy_url=f"http://127.0.0.1:{_FRONTEND_PORT}",
    telemetry_enabled=False,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(appearance="light", accent_color="blue", radius="medium")
        ),
    ],
)
