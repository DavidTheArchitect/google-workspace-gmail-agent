"""No-argument entry point for the Reflex operator console."""

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from compliance_agent.settings import load_settings
from compliance_agent.startup import choose_console_port, console_public_origin


def run(arguments: list[str]) -> int:
    """Compatibility-friendly no-argument launcher command."""

    if arguments != ["console"]:
        message = "gmail-agent accepts only its internal console command"
        raise ValueError(message)
    return _run_reflex_console()


def _run_reflex_console() -> int:
    settings = load_settings()
    repository = Path.cwd().resolve()
    node_dir = resolve_node_directory(repository)
    preferred_port = settings.console_port
    selected_port = choose_console_port(preferred_port)
    if selected_port != preferred_port:
        sys.stdout.write(f"Console port {preferred_port} is busy; using {selected_port} instead.\n")
    environment = dict(os.environ)
    if node_dir is not None:
        environment["PATH"] = f"{node_dir}{os.pathsep}{environment.get('PATH', '')}"
    environment["GMAIL_AGENT_CONSOLE_PORT"] = str(selected_port)
    environment["GMAIL_AGENT_CONSOLE_BACKEND_PORT"] = str(selected_port)
    environment["GMAIL_AGENT_CONSOLE_HOST"] = settings.console_bind_host.value
    url = console_public_origin(selected_port)
    environment["GMAIL_AGENT_PUBLIC_URL"] = url
    if settings.console_open_browser:
        opener = threading.Thread(
            target=_open_when_ready,
            args=(selected_port, url),
            daemon=True,
            name="reflex-console-opener",
        )
        opener.start()
    command = [
        sys.executable,
        "-m",
        "reflex",
        "run",
        "--env",
        "prod",
        "--frontend-port",
        str(selected_port),
        "--backend-port",
        str(selected_port),
        "--backend-host",
        settings.console_bind_host.value,
        "--single-port",
    ]
    return subprocess.call(command, env=environment)  # noqa: S603


def resolve_node_directory(repository: Path, *, platform_name: str | None = None) -> Path | None:
    """Prefer the pinned platform runtime, then accept Node and npm supplied externally."""

    effective_platform = sys.platform if platform_name is None else platform_name
    if effective_platform == "win32":
        node_dir = repository / ".node" / "node-v22.22.3-win-x64"
        if (node_dir / "node.exe").is_file():
            return node_dir
    if effective_platform.startswith("linux"):
        architecture = platform.machine().lower()
        target = "linux-arm64" if architecture in {"aarch64", "arm64"} else "linux-x64"
        node_dir = repository / ".node" / f"node-v22.22.3-{target}" / "bin"
        if (node_dir / "node").is_file():
            return node_dir
    if shutil.which("node") is not None and shutil.which("npm") is not None:
        return None
    message = (
        "Node.js 22 or newer is required, together with npm; run the platform setup script "
        "or use the published container image"
    )
    raise SystemExit(message)


def main() -> None:
    """Start loopback-only Reflex services and open the operator surface."""

    raise SystemExit(run(["console"]))


def _open_when_ready(port: int, url: str) -> None:
    for _attempt in range(120):
        with socket.socket() as connection:
            connection.settimeout(0.25)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.25)
