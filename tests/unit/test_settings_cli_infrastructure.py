"""Configuration, direct commands, ownership persistence, locks, and injected adapters."""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from compliance_agent import launcher
from compliance_agent.cli import _open_console_when_ready, run
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import RunLockUnavailable
from compliance_agent.infrastructure import permissions
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.identifiers import Uuid4Generator
from compliance_agent.infrastructure.permissions import (
    directory_is_accessible,
    restrict_permissions,
)
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.settings import ConsoleBindHost, Settings
from compliance_agent.startup import (
    choose_console_port,
    collect_startup_checks,
    console_public_origin,
    format_startup_checks,
    ollama_available,
    port_available,
)
from tests.conftest import OWNERSHIP_ID, registry_for

if TYPE_CHECKING:
    import uvicorn


class PermissionRecorder:
    def __init__(self) -> None:
        self.modes: list[int] = []

    def chmod(self, mode: int) -> None:
        self.modes.append(mode)


def test_sensitive_permission_modes_are_posix_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = PermissionRecorder()
    monkeypatch.setattr(permissions, "os", SimpleNamespace(name="nt"))
    restrict_permissions(target, 0o600)  # type: ignore[arg-type]
    assert target.modes == []

    monkeypatch.setattr(permissions, "os", SimpleNamespace(name="posix"))
    restrict_permissions(target, 0o600)  # type: ignore[arg-type]
    assert target.modes == [0o600]
    assert directory_is_accessible(tmp_path)
    assert directory_is_accessible(tmp_path / "not-created")


def _settings_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "profile_dir": tmp_path / "profile",
        "audit_dir": tmp_path / "audit",
        "state_dir": tmp_path / "state",
    }


def test_settings_accept_safe_defaults_and_reject_live_headless_or_missing_identity(
    tmp_path: Path,
) -> None:
    settings = Settings(**_settings_paths(tmp_path))
    assert settings.dry_run
    assert settings.plan_only
    assert str(settings.gmail_settings_url) == "https://admin.google.com/ac/apps/gmail/spam"
    assert settings.console_bind_host == ConsoleBindHost.LOOPBACK
    with pytest.raises(ValidationError, match="headed browser"):
        Settings(
            **_settings_paths(tmp_path),
            dry_run=False,
            plan_only=False,
            headless=True,
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    with pytest.raises(ValidationError, match="EXPECTED_ADMIN"):
        Settings(
            **_settings_paths(tmp_path),
            dry_run=False,
            plan_only=False,
            expected_admin_email="",
            expected_workspace_domain="",
        )


def test_settings_reject_relative_or_overlapping_sensitive_paths(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must be absolute"):
        Settings(profile_dir=Path("relative"))
    with pytest.raises(ValidationError, match="must be distinct"):
        Settings(
            profile_dir=tmp_path,
            audit_dir=tmp_path,
            state_dir=tmp_path / "state",
        )
    with pytest.raises(ValidationError, match="non-overlapping"):
        Settings(
            profile_dir=tmp_path / "sensitive",
            audit_dir=tmp_path / "sensitive" / "audit",
            state_dir=tmp_path / "state",
        )


def test_settings_normalize_identity_and_restrict_security_sensitive_hosts(tmp_path: Path) -> None:
    settings = Settings(
        **_settings_paths(tmp_path),
        expected_admin_email=" Admin@Example.COM ",
        expected_workspace_domain=" Example.COM ",
        managed_resource_prefix="  [Managed]  ",
    )

    assert settings.expected_admin_email == "admin@example.com"
    assert settings.expected_workspace_domain == "example.com"
    assert settings.managed_resource_prefix == "[Managed]"
    with pytest.raises(ValidationError, match="loopback"):
        Settings(**_settings_paths(tmp_path), ollama_base_url="https://ollama.example.com/v1")
    container_settings = Settings(
        **_settings_paths(tmp_path),
        console_bind_host=ConsoleBindHost.CONTAINER,
        ollama_base_url="http://host.docker.internal:11434/v1",
    )
    assert container_settings.console_bind_host == ConsoleBindHost.CONTAINER
    with pytest.raises(ValidationError, match="console_bind_host"):
        Settings(**_settings_paths(tmp_path), console_bind_host="192.0.2.1")
    with pytest.raises(ValidationError, match=r"admin\.google\.com"):
        Settings(**_settings_paths(tmp_path), gmail_settings_url="https://example.com/settings")


def test_ownership_store_round_trips_validated_registry(tmp_path: Path) -> None:
    store = OwnershipStore(tmp_path / "state")
    assert store.load() == OwnershipRegistry()

    store.save(registry_for())

    assert store.load().resources[0].ownership_id == OWNERSHIP_ID


def test_process_lock_prohibits_concurrent_runs(tmp_path: Path) -> None:
    path = tmp_path / "state" / "run.lock"
    started = datetime(2026, 7, 10, 18, 30, tzinfo=UTC)
    first = ProcessLock(path, run_id="one", started_at=started, application_version="0.1.0")
    second = ProcessLock(path, run_id="two", started_at=started, application_version="0.1.0")

    with first:
        with pytest.raises(RunLockUnavailable):
            second.acquire()
        with pytest.raises(RunLockUnavailable, match="already owns"):
            first.acquire()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["run_id"] == "one"
    second.acquire()
    second.release()


def test_clock_and_identifier_sources_return_expected_types() -> None:
    assert SystemClock().now().tzinfo is not None
    assert Uuid4Generator().new().version == 4


def test_direct_cli_commands_emit_the_common_typed_plan(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["block", "add", "--domain", "Example.COM", "--notice", "Rejected"]) == 0
    add_plan = json.loads(capsys.readouterr().out)
    assert add_plan["actions"][0]["entries"][0]["normalized_value"] == "example.com"

    assert run(["block", "list"]) == 0
    assert json.loads(capsys.readouterr().out)["actions"][0]["type"] == "list_blocked_sender_rules"

    assert (
        run(
            [
                "block",
                "remove",
                "--domain",
                "example.com",
                "--rule-id",
                str(OWNERSHIP_ID),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["actions"][0]["type"] == "remove_blocked_entries"


def test_rule_cli_validation_and_version_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        run(
            [
                "rule",
                "set-notice",
                "--rule-id",
                str(OWNERSHIP_ID),
                "--notice",
                "New notice",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        run(
            [
                "rule",
                "remove",
                "--rule-id",
                str(OWNERSHIP_ID),
                "--remove-owned-address-list",
            ]
        )
        == 0
    )
    plan_text = capsys.readouterr().out
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan_text, encoding="utf-8")
    assert run(["validate-plan", str(plan_path)]) == 0
    capsys.readouterr()
    assert run(["version"]) == 0
    assert json.loads(capsys.readouterr().out)["compliance-agent"] == "0.1.0"


def test_cli_reports_invalid_direct_input_without_opening_browser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(["block", "add", "--domain", "https://example.com"]) == 2
    assert "URL schemes" in capsys.readouterr().err
    assert run(["block", "add"]) == 2
    assert "provide at least one" in capsys.readouterr().err


def test_startup_checks_explain_port_fallback_and_optional_ollama(tmp_path: Path) -> None:
    settings = Settings(console_port=8765, **_settings_paths(tmp_path))

    selected = choose_console_port(8765, available=lambda port: port == 8767)
    checks = collect_startup_checks(
        settings,
        available=lambda port: port == 8767,
        ollama_probe=lambda _settings: False,
    )
    report = format_startup_checks(checks)

    assert selected == 8767
    assert "8765 is busy; startup will use 8767 automatically" in report
    assert "primary deterministic form will still work" in report
    assert "Ready to start" in report


def test_startup_checks_cover_available_services_and_disabled_browser(tmp_path: Path) -> None:
    settings = Settings(
        console_port=8765,
        console_open_browser=False,
        **_settings_paths(tmp_path),
    )

    checks = collect_startup_checks(
        settings,
        available=lambda port: port == 8765,
        ollama_probe=lambda _settings: True,
    )
    report = format_startup_checks(checks)

    assert "127.0.0.1:8765 is available" in report
    assert "Local natural-language planning is available" in report
    assert "Automatic browser opening is disabled" in report


def test_socket_probes_detect_busy_port_and_local_ollama(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        settings = Settings(
            ollama_base_url=f"http://127.0.0.1:{port}/v1",
            **_settings_paths(tmp_path),
        )

        assert not port_available(port)
        assert ollama_available(settings)

    assert port_available(port)
    assert not ollama_available(settings)


def test_port_selection_fails_after_bounded_search() -> None:
    with pytest.raises(OSError, match="no free console port"):
        choose_console_port(65_535, available=lambda _port: False)


def test_console_public_origin_is_exact_for_local_and_codespaces() -> None:
    assert console_public_origin(8765, environ={}) == "http://127.0.0.1:8765"
    codespaces = {
        "CODESPACES": "true",
        "CODESPACE_NAME": "careful-console",
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
    }

    assert (
        console_public_origin(8765, environ=codespaces)
        == "https://careful-console-8765.app.github.dev"
    )

    codespaces["CODESPACE_NAME"] = "bad.example"
    with pytest.raises(ValueError, match="missing or invalid"):
        console_public_origin(8765, environ=codespaces)


def test_doctor_command_prints_human_readable_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, path in _settings_paths(tmp_path).items():
        monkeypatch.setenv(f"CA_{name.upper()}", str(path))
    monkeypatch.setenv("CA_CONSOLE_PORT", "65432")

    assert run(["doctor"]) == 0

    output = capsys.readouterr().out
    assert "Gmail Compliance Agent startup check" in output
    assert "[PASS] Configuration" in output
    assert "[WARN] Ollama" in output or "[PASS] Ollama" in output


def test_console_uses_automatic_port_fallback(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeServer:
        started = False
        should_exit = False

        def __init__(self, config: object) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("compliance_agent.cli.choose_console_port", lambda _port: 8766)
    monkeypatch.setattr("compliance_agent.cli.uvicorn.Server", FakeServer)

    assert run(["console", "--no-open"]) == 0

    assert captured["ran"] is True
    assert cast("uvicorn.Config", captured["config"]).port == 8766
    assert cast("uvicorn.Config", captured["config"]).host == "127.0.0.1"
    assert "using 8766 instead" in capsys.readouterr().out


def test_console_uses_container_bind_host_when_explicitly_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeServer:
        started = False
        should_exit = False

        def __init__(self, config: object) -> None:
            captured["config"] = config

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setenv("CA_CONSOLE_BIND_HOST", ConsoleBindHost.CONTAINER.value)
    monkeypatch.setattr("compliance_agent.cli.port_available", lambda _port: True)
    monkeypatch.setattr("compliance_agent.cli.uvicorn.Server", FakeServer)

    assert run(["console", "--port", "8765", "--no-open"]) == 0

    assert captured["ran"] is True
    assert cast("uvicorn.Config", captured["config"]).host == ConsoleBindHost.CONTAINER.value


def test_explicit_busy_console_port_has_clear_recovery(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("compliance_agent.cli.port_available", lambda _port: False)

    assert run(["console", "--port", "8765", "--no-open"]) == 2

    assert "omit --port" in capsys.readouterr().err


def test_console_opener_waits_for_ready_server_and_uses_new_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = cast(
        "uvicorn.Server",
        SimpleNamespace(started=True, should_exit=False),
    )
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "compliance_agent.cli.webbrowser.open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    _open_console_when_ready(server, "http://127.0.0.1:8765/bootstrap#secret")

    assert opened == [("http://127.0.0.1:8765/bootstrap#secret", 2)]


def test_console_opener_stops_without_opening_when_server_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = cast(
        "uvicorn.Server",
        SimpleNamespace(started=False, should_exit=True),
    )
    opened: list[str] = []
    monkeypatch.setattr(
        "compliance_agent.cli.webbrowser.open",
        lambda url, new=0: opened.append(url) or True,
    )

    _open_console_when_ready(server, "http://127.0.0.1:8765/bootstrap#secret")

    assert not opened


def test_console_opener_reports_browser_failure(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = cast(
        "uvicorn.Server",
        SimpleNamespace(started=True, should_exit=False),
    )
    monkeypatch.setattr("compliance_agent.cli.webbrowser.open", lambda url, new=0: False)

    _open_console_when_ready(server, "http://127.0.0.1:8765/bootstrap#secret")

    assert "secure fallback link" in capsys.readouterr().err


def test_short_launcher_starts_console(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(launcher, "run", lambda args: calls.append(args) or 0)

    with pytest.raises(SystemExit, match="0"):
        launcher.main()

    assert calls == [["console"]]


def test_reflex_launcher_propagates_automatic_port_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_dir = tmp_path / ".node" / "node-v22.22.3-win-x64"
    node_dir.mkdir(parents=True)
    (node_dir / "node.exe").touch()
    captured: dict[str, object] = {}

    class FakeThread:
        def __init__(
            self,
            *,
            target: object,
            args: tuple[int, str],
            daemon: bool,
            name: str,
        ) -> None:
            captured["thread"] = (target, args, daemon, name)

        def start(self) -> None:
            captured["thread_started"] = True

    def fake_call(command: list[str], *, env: dict[str, str]) -> int:
        captured["command"] = command
        captured["environment"] = env
        return 17

    monkeypatch.setattr(
        launcher,
        "load_settings",
        lambda: SimpleNamespace(console_port=8765, console_open_browser=True),
    )
    monkeypatch.setattr(launcher, "choose_console_port", lambda _port: 8766)
    monkeypatch.setattr(
        launcher,
        "console_public_origin",
        lambda port: f"http://127.0.0.1:{port}",
    )
    monkeypatch.setattr(launcher.Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher.subprocess, "call", fake_call)

    assert launcher._run_reflex_console() == 17

    command = cast("list[str]", captured["command"])
    environment = cast("dict[str, str]", captured["environment"])
    assert command[command.index("--frontend-port") + 1] == "8766"
    assert command[command.index("--backend-port") + 1] == "8766"
    assert environment["GMAIL_AGENT_CONSOLE_PORT"] == "8766"
    assert environment["GMAIL_AGENT_CONSOLE_BACKEND_PORT"] == "8766"
    assert environment["GMAIL_AGENT_PUBLIC_URL"] == "http://127.0.0.1:8766"
    assert captured["thread"] == (
        launcher._open_when_ready,
        (8766, "http://127.0.0.1:8766"),
        True,
        "reflex-console-opener",
    )
    assert captured["thread_started"] is True
    assert "port 8765 is busy; using 8766 instead" in capsys.readouterr().out


def test_reflex_launcher_respects_disabled_browser_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_dir = tmp_path / ".node" / "node-v22.22.3-win-x64"
    node_dir.mkdir(parents=True)
    (node_dir / "node.exe").touch()
    thread_started = False

    class FakeThread:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal thread_started
            thread_started = True

    monkeypatch.setattr(
        launcher,
        "load_settings",
        lambda: SimpleNamespace(console_port=8765, console_open_browser=False),
    )
    monkeypatch.setattr(launcher, "choose_console_port", lambda port: port)
    monkeypatch.setattr(
        launcher,
        "console_public_origin",
        lambda port: f"http://127.0.0.1:{port}",
    )
    monkeypatch.setattr(launcher.Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher.subprocess, "call", lambda _command, *, env: 0)

    assert launcher._run_reflex_console() == 0
    assert not thread_started


def test_cli_audit_prune_defaults_to_plan_and_requires_apply(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _settings_paths(tmp_path)
    monkeypatch.setenv("CA_PROFILE_DIR", str(paths["profile_dir"]))
    monkeypatch.setenv("CA_AUDIT_DIR", str(paths["audit_dir"]))
    monkeypatch.setenv("CA_STATE_DIR", str(paths["state_dir"]))
    expired = paths["audit_dir"] / "runs" / f"20000101T000000Z-{'0' * 32}"
    expired.mkdir(parents=True)
    paths["audit_dir"].chmod(0o700)

    assert run(["audit", "prune"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["candidate_count"] == 1
    assert not planned["applied"]
    assert expired.exists()

    assert run(["audit", "prune", "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["applied"]
    assert not expired.exists()
