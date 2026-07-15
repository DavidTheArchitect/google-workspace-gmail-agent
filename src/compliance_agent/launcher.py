"""No-argument entry point for the Reflex operator console."""

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from compliance_agent.settings import load_settings


def run(arguments: list[str]) -> int:
    """Compatibility-friendly no-argument launcher command."""

    if arguments != ["console"]:
        message = "gmail-agent accepts only its internal console command"
        raise ValueError(message)
    return _run_reflex_console()


def _run_reflex_console() -> int:
    settings = load_settings()
    repository = Path.cwd().resolve()
    node_dir = repository / ".node" / "node-v22.22.3-win-x64"
    if not (node_dir / "node.exe").is_file():
        message = "project-local Node is missing; run Setup-Gmail-Agent.cmd"
        raise SystemExit(message)
    environment = dict(os.environ)
    environment["PATH"] = f"{node_dir}{os.pathsep}{environment.get('PATH', '')}"
    url = f"http://127.0.0.1:{settings.console_port}"
    opener = threading.Thread(
        target=_open_when_ready,
        args=(settings.console_port, url),
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
        str(settings.console_port),
        "--backend-port",
        str(settings.console_port),
        "--backend-host",
        "127.0.0.1",
        "--single-port",
    ]
    return subprocess.call(command, env=environment)  # noqa: S603


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
