"""Windows setup and start launcher contracts."""

from pathlib import Path


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


def test_example_environment_is_safe_and_documents_optional_ollama() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert "# CA_RUN_MODE=plan_only" in example
    assert "CA_CONSOLE_OPEN_BROWSER=true" in example
    assert "The deterministic form works without Ollama" in example
    assert "CA_EXPECTED_ADMIN_EMAIL" in example
