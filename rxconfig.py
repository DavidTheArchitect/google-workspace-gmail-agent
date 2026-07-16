"""Reflex development and production configuration."""

import os

import reflex as rx

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
