"""Configuration, direct commands, ownership persistence, locks, and injected adapters."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from compliance_agent.cli import run
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import RunLockUnavailable
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.identifiers import Uuid4Generator
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.settings import Settings
from tests.conftest import OWNERSHIP_ID, registry_for


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
        Settings(**_settings_paths(tmp_path), dry_run=False, plan_only=False)


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
