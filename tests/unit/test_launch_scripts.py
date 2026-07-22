"""Setup and launcher contracts."""

from pathlib import Path

import pytest

from compliance_agent import launcher
from scripts.install_node import resolve_distribution


def test_setup_script_creates_config_and_uses_official_winget_package() -> None:
    script = Path("Setup-Gmail-Agent.cmd").read_text(encoding="utf-8")

    assert 'copy /Y ".env.example" ".env"' in script
    assert "winget install --id=astral-sh.uv -e" in script
    assert "uv sync --locked --extra dev" in script
    assert "compliance-agent doctor" in script
    assert 'set "PYTHONUNBUFFERED=1"' in script


def test_start_script_repairs_missing_environment_and_skips_runtime_sync() -> None:
    script = Path("Start-Gmail-Agent.cmd").read_text(encoding="utf-8")

    assert 'if not exist ".venv\\Scripts\\gmail-agent.exe"' in script
    assert "uv sync --locked --extra dev" in script
    assert "uv run --no-sync compliance-agent doctor" in script
    assert "uv run --no-sync gmail-agent" in script
    assert 'set "PYTHONUNBUFFERED=1"' in script


def test_linux_setup_and_start_scripts_share_the_locked_runtime_contract() -> None:
    setup = Path("Setup-Gmail-Agent.sh").read_text(encoding="utf-8")
    start = Path("Start-Gmail-Agent.sh").read_text(encoding="utf-8")

    assert "cp .env.example .env" in setup
    assert "uv sync --locked --extra dev" in setup
    assert "python scripts/install_node.py" in setup
    assert "compliance-agent doctor" in setup
    assert "uv sync --locked --extra dev" in start
    assert "python scripts/install_node.py" in start
    assert "uv run --no-sync gmail-agent" in start


def test_example_environment_is_safe_and_documents_container_ollama() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert "# CA_RUN_MODE=plan_only" in example
    assert "CA_CONSOLE_OPEN_BROWSER=true" in example
    assert "OLLAMA_BASE_URL=http://ollama:11434/v1" in example
    assert "OLLAMA_MODEL=gemma4:12b" in example
    assert "CA_EXPECTED_ADMIN_EMAIL" in example


def test_launcher_prefers_project_node_on_windows(tmp_path: Path) -> None:
    node_dir = tmp_path / ".node" / "node-v22.22.3-win-x64"
    node_dir.mkdir(parents=True)
    (node_dir / "node.exe").touch()

    assert launcher.resolve_node_directory(tmp_path, platform_name="win32") == node_dir


def test_launcher_uses_node_from_path_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda executable: f"/usr/bin/{executable}")

    assert launcher.resolve_node_directory(tmp_path, platform_name="linux") is None


def test_launcher_prefers_project_node_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    node_dir = tmp_path / ".node" / "node-v22.22.3-linux-x64" / "bin"
    node_dir.mkdir(parents=True)
    (node_dir / "node").touch()
    monkeypatch.setattr(launcher.platform, "machine", lambda: "x86_64")

    assert launcher.resolve_node_directory(tmp_path, platform_name="linux") == node_dir


def test_launcher_reports_missing_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda _executable: None)

    with pytest.raises(SystemExit, match=r"Node\.js 22 or newer is required"):
        launcher.resolve_node_directory(tmp_path, platform_name="linux")


@pytest.mark.parametrize(
    ("platform_name", "machine", "archive_name", "executable"),
    [
        ("win32", "AMD64", "node-v22.22.3-win-x64.zip", Path("node.exe")),
        ("linux", "x86_64", "node-v22.22.3-linux-x64.tar.xz", Path("bin/node")),
        ("linux", "aarch64", "node-v22.22.3-linux-arm64.tar.xz", Path("bin/node")),
    ],
)
def test_node_installer_resolves_supported_platforms(
    platform_name: str,
    machine: str,
    archive_name: str,
    executable: Path,
) -> None:
    distribution = resolve_distribution(platform_name=platform_name, machine=machine)

    assert distribution.archive_name == archive_name
    assert distribution.executable_path == executable


def test_node_installer_rejects_unsupported_platform() -> None:
    with pytest.raises(RuntimeError, match="unsupported Node platform"):
        resolve_distribution(platform_name="darwin", machine="arm64")
