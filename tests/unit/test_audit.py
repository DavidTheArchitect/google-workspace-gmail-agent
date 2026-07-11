"""Protected audit writes, hash chains, manifests, reports, and redacted export."""

import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.audit.export import export_redacted
from compliance_agent.audit.manifest import (
    RunManifest,
    RunManifestMetadata,
    digest_artifacts,
    verify_manifest,
)
from compliance_agent.audit.redaction import redact_text
from compliance_agent.audit.report import render_report_json, render_report_markdown
from compliance_agent.audit.writer import RunAuditWriter, verify_event_chain
from compliance_agent.exceptions import AuditWriteFailure
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.status import RunStatus


def _event(sequence: int, *, previous_hash: str | None = None) -> AuditEvent:
    return AuditEvent(
        run_id="run-1",
        sequence=sequence,
        timestamp=datetime(2026, 7, 10, 18, 30, sequence, tzinfo=UTC),
        event_type="test_event",
        component="test",
        outcome="ok",
        previous_event_hash=previous_hash,
    )


def _manifest_metadata() -> RunManifestMetadata:
    start = datetime(2026, 7, 10, 18, 30, tzinfo=UTC)
    return RunManifestMetadata(
        application_version="0.1.0",
        git_commit=None,
        dirty_working_tree=True,
        python_version=platform.python_version(),
        agent_framework_version="1.11.0",
        playwright_version="1.61.0",
        browser_version=None,
        pydantic_version="2.13.4",
        ollama_version=None,
        model_tag=None,
        model_digest=None,
        operating_system=platform.platform(),
        start_time=start,
    )


def _manifest(run_directory: Path) -> RunManifest:
    metadata = _manifest_metadata()
    return RunManifest(
        **metadata.model_dump(),
        end_time=metadata.start_time + timedelta(seconds=1),
        final_status=RunStatus.NO_CHANGE_REQUIRED,
        artifacts=digest_artifacts(run_directory),
    )


def test_writer_builds_sequence_checked_hash_chain_and_atomic_artifacts(tmp_path: Path) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    first = writer.append(_event(1))
    second = writer.append(_event(2))
    artifact = writer.write_text("diagnostics/result.txt", "safe")

    assert first.event_hash
    assert second.previous_event_hash == first.event_hash
    assert artifact.read_text(encoding="utf-8") == "safe"
    assert not verify_event_chain(writer.run_directory / "run.jsonl")


class FixedClock:
    """Return one deterministic aware timestamp."""

    def now(self) -> datetime:
        return datetime(2026, 7, 10, 18, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_audit_finalizer_writes_reports_and_terminal_event(
    tmp_path: Path,
) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    service = AuditFinalizationService(writer, FixedClock(), "run-1", _manifest_metadata())
    result = RunResult(status=RunStatus.NO_CHANGE_REQUIRED)

    await service.finalize(result)

    assert json.loads((writer.run_directory / "report.json").read_text())["status"] == (
        "no_change_required"
    )
    assert "no_change_required" in (writer.run_directory / "report.md").read_text()
    assert writer.next_sequence == 2
    assert not verify_event_chain(writer.run_directory / "run.jsonl")
    manifest = RunManifest.model_validate_json(
        (writer.run_directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.final_status == RunStatus.NO_CHANGE_REQUIRED
    assert not verify_manifest(writer.run_directory, manifest)


def test_writer_rejects_sequence_hash_and_path_traversal(tmp_path: Path) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    with pytest.raises(AuditWriteFailure, match="expected audit sequence"):
        writer.append(_event(2))
    first = writer.append(_event(1))
    assert first.event_hash
    with pytest.raises(AuditWriteFailure, match="previous hash"):
        writer.append(_event(2, previous_hash="0" * 64))
    with pytest.raises(AuditWriteFailure, match="run ID changed"):
        writer.append(_event(2).model_copy(update={"run_id": "different-run"}))
    with pytest.raises(AuditWriteFailure, match="run-relative"):
        writer.write_text("../escape.txt", "no")


def test_event_chain_detects_tampering_and_invalid_json(tmp_path: Path) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    writer.append(_event(1))
    path = writer.run_directory / "run.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["outcome"] = "tampered"
    path.write_text(json.dumps(payload) + "\nnot json\n", encoding="utf-8")

    errors = verify_event_chain(path)

    assert any("event hash mismatch" in error for error in errors)
    assert any("invalid event" in error for error in errors)
    assert verify_event_chain(tmp_path / "missing.jsonl")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert verify_event_chain(empty) == ("event stream is empty",)


def test_manifest_detects_changed_added_and_missing_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    first = run / "report.json"
    first.write_text("{}", encoding="utf-8")
    manifest = _manifest(run)

    assert not verify_manifest(run, manifest)
    first.write_text('{"changed":true}', encoding="utf-8")
    (run / "extra.txt").write_text("extra", encoding="utf-8")

    mismatches = verify_manifest(run, manifest)
    assert mismatches == ("extra.txt", "report.json")


def test_manifest_rejects_duplicate_paths_and_invalid_timing(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "report.json").write_text("{}", encoding="utf-8")
    manifest = _manifest(run)
    payload = manifest.model_dump()
    payload["artifacts"] = [manifest.artifacts[0], manifest.artifacts[0]]
    with pytest.raises(ValidationError, match="duplicate artifact"):
        RunManifest.model_validate(payload)
    payload = manifest.model_dump()
    payload["end_time"] = manifest.start_time - timedelta(seconds=1)
    with pytest.raises(ValidationError, match="cannot precede"):
        RunManifest.model_validate(payload)


def test_redacted_export_does_not_copy_binary_or_authentication_material(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "report.txt").write_text(
        "Authorization: Bearer secret\nCookie: SID=secret\nadmin@example.com",
        encoding="utf-8",
    )
    (source / "trace.zip").write_bytes(b"secret binary")
    (source / "page.html").write_text(
        '<script>secret()</script><div onload="secret()">admin@example.com</div>',
        encoding="utf-8",
    )
    destination = tmp_path / "export"

    export_redacted(source, destination)

    exported = (destination / "report.txt").read_text(encoding="utf-8")
    assert "secret" not in exported
    assert "a***@example.com" in exported
    exported_html = (destination / "page.html").read_text(encoding="utf-8")
    assert "script" not in exported_html
    assert "onload" not in exported_html
    assert not (destination / "trace.zip").exists()
    with pytest.raises(AuditWriteFailure, match="already exists"):
        export_redacted(source, destination)
    with pytest.raises(AuditWriteFailure, match="regular directory"):
        export_redacted(tmp_path / "missing", tmp_path / "other")
    with pytest.raises(AuditWriteFailure, match="inside"):
        export_redacted(source, source / "nested-export")


def test_redaction_handles_token_fields_and_reports_are_deterministic() -> None:
    redacted_json = redact_text('{"access_token":"secret","next":"ok"}')
    assert json.loads(redacted_json) == {"access_token": "[REDACTED]", "next": "ok"}
    redacted_headers = redact_text(
        '{"Authorization":"Bearer abc123","Cookie":"SID=secret","refresh_token":null}'
    )
    assert json.loads(redacted_headers) == {
        "Authorization": "[REDACTED]",
        "Cookie": "[REDACTED]",
        "refresh_token": "[REDACTED]",
    }
    assert "abc123" not in redact_text("Authorization: Basic abc123")
    assert "secret" not in redact_text("https://example.test/?access_token=secret&next=1")
    result = RunResult(
        status=RunStatus.APPLIED_PENDING_PROPAGATION,
        requested_changes=("add example.com",),
        verified_changes=("example.com present",),
        warnings=("Propagation pending",),
        propagation_pending=True,
    )

    report_json = render_report_json(result)
    report_markdown = render_report_markdown(result)

    assert json.loads(report_json)["status"] == "applied_pending_propagation"
    assert "## Requested changes" in report_markdown
    assert "## Verified changes" in report_markdown
    assert "## Warnings" in report_markdown


def test_audit_event_rejects_naive_timestamp() -> None:
    payload = _event(1).model_dump()
    payload["timestamp"] = datetime(2026, 7, 10)  # noqa: DTZ001 - intentional invalid input.
    with pytest.raises(ValueError, match="timezone-aware"):
        AuditEvent.model_validate(payload)
