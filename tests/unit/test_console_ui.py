"""Console UX enhancements: live updates, styled errors, audit depth, recovery."""

import asyncio
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.audit_inspection_service import inspect_audit_run
from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.dry_run_audit_service import DryRunAuditFinalizationService
from compliance_agent.application.dry_run_service import DryRunDependencies, DryRunService
from compliance_agent.application.ownership_console_service import (
    health_with_recoverability,
    latest_observed_state,
)
from compliance_agent.application.planning_service import direct_add_plan
from compliance_agent.application.workflow_audit_service import WorkflowAuditService
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.console import create_console_app
from compliance_agent.console.app import ConsoleApplication
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
)
from compliance_agent.console.readiness import ReadinessCache, greeting_for_hour
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import AuditRetentionFailure, AuditWriteFailure
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.schemas.operations import PhaseTransition, RunMode, RunPhase
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.settings import Settings
from tests.conftest import SECOND_ID, domain, owned_state
from tests.unit.test_console_enhancements import (
    NOW,
    FailingPlanner,
    FixedClock,
    FixedIdentifiers,
    MemoryOwnershipStore,
    ReadyPreflight,
    StaticPlanner,
    StaticReader,
    _connect,
    _metadata,
    _settings,
)


class HangingPlanner:
    async def create_plan(self, _request_text: str) -> None:
        await asyncio.Event().wait()


class SettableClock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def now(self) -> datetime:
        return self.moment


async def _finalized_dry_run(settings: Settings, run_id: str) -> Path:
    """Create one integrity-valid finalized dry-run under the settings audit dir."""

    run_directory = settings.audit_dir / "runs" / f"20260711T150000Z-{run_id}"
    writer = RunAuditWriter(run_directory)
    service = DryRunService(
        DryRunDependencies(
            preflight=ReadyPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=WorkflowAuditService(writer, FixedClock(), run_id),
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )
    result = await service.preview(direct_add_plan((domain("new.example"),), "Mail rejected."))
    await DryRunAuditFinalizationService(writer, FixedClock(), run_id, _metadata()).finalize(result)
    return run_directory


async def _finalized_observation(
    settings: Settings,
    run_id: str,
    state: BlockedSenderState,
) -> Path:
    """Finalize a run that recorded an after-state observation."""

    run_directory = settings.audit_dir / "runs" / f"20260711T150000Z-{run_id}"
    writer = RunAuditWriter(run_directory)
    auditor = WorkflowAuditService(writer, FixedClock(), run_id)
    auditor.record_state("after", state)
    await AuditFinalizationService(writer, FixedClock(), run_id, _metadata()).finalize(
        RunResult(status=RunStatus.NO_CHANGE_REQUIRED)
    )
    return run_directory


def _client(console: ConsoleApplication) -> TestClient:
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")
    _connect(client, console.security.launch_token)
    return client


def _coordinator(planner: object, seed: int) -> ConsoleCoordinator:
    return ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=planner,  # type: ignore[arg-type]
            identifiers=FixedIdentifiers(UUID(int=seed)),
            clock=FixedClock().now,
            approval_service=ApprovalService(600),
        )
    )


def test_error_pages_render_app_chrome_with_security_headers(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    missing_run = client.get(f"/runs/{'f' * 32}")
    missing_audit = client.get(f"/audits/{'f' * 32}")
    missing_page = client.get("/no-such-page")
    forbidden = client.post(
        "/runs",
        data={"request_text": "Block x.example", "mode": "plan_only", "csrf_token": "bad"},
    )
    bad_value = client.post(f"/runs/{'f' * 32}/cancel", data={"csrf_token": csrf})

    assert missing_run.status_code == 404
    assert "Run not found" in missing_run.text
    assert 'class="topbar"' in missing_run.text
    assert "frame-ancestors 'none'" in missing_run.headers["content-security-policy"]
    assert missing_audit.status_code == 404
    assert missing_page.status_code == 404
    assert "Page not found" in missing_page.text
    assert forbidden.status_code == 403
    assert "Not permitted" in forbidden.text
    assert bad_value.status_code == 400
    assert "Request refused" in bad_value.text


def test_browser_form_posts_pass_origin_check_and_null_stays_closed(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()
    data = {"request_text": "Block new.example", "mode": "plan_only", "csrf_token": csrf}

    dashboard = client.get("/")
    same_origin = client.post(
        "/runs",
        data=data,
        headers={"origin": "http://127.0.0.1:8765"},
        follow_redirects=False,
    )
    null_origin = client.post("/runs", data=data, headers={"origin": "null"})

    # same-origin form POSTs must work in a real browser, so the referrer
    # policy cannot be no-referrer (that serializes Origin as "null").
    assert dashboard.headers["referrer-policy"] == "same-origin"
    assert same_origin.status_code == 303
    assert null_origin.status_code == 403


def test_invalid_bootstrap_token_gets_styled_error(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")

    response = client.post("/bootstrap", data={"token": "wrong"})

    assert response.status_code == 403
    assert "Invalid launch token" in response.text
    assert 'class="topbar"' in response.text


def test_unexpected_error_renders_styled_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())

    def boom(_run_id: str) -> None:
        message = "controlled failure"
        raise RuntimeError(message)

    monkeypatch.setattr(console.coordinator, "get", boom)
    client = TestClient(
        console.app,
        base_url="http://127.0.0.1:8765",
        raise_server_exceptions=False,
    )
    _connect(client, console.security.launch_token)

    response = client.get(f"/runs/{'f' * 32}")

    assert response.status_code == 500
    assert "Unexpected error" in response.text
    assert "controlled failure" not in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_sse_stream_reports_unknown_runs_as_gone(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    events = client.get(f"/runs/{'f' * 32}/events")

    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: gone" in events.text


def test_sse_stream_heartbeats_then_times_out_for_stuck_run(tmp_path: Path) -> None:
    console = create_console_app(
        _settings(tmp_path),
        planner=HangingPlanner(),
        sse_poll_seconds=0,
        sse_max_polls=2,
    )
    client = _client(console)
    csrf = console.security.csrf_token()

    created = client.post(
        "/runs",
        data={"request_text": "Block slow.example", "mode": "plan_only", "csrf_token": csrf},
        follow_redirects=False,
    )
    run_url = created.headers["location"]
    detail = client.get(run_url)
    events = client.get(f"{run_url}/events")

    assert "sse-connect" in detail.text
    assert "Planning with the local model" in detail.text
    assert "event: phase" in events.text
    assert ": keep-alive" in events.text
    assert "data: timeout" in events.text


@pytest.mark.asyncio
async def test_coordinator_split_planning_records_history() -> None:
    coordinator = _coordinator(StaticPlanner(), 7)

    started = coordinator.start("Block new.example", RunMode.PLAN_ONLY)
    assert started.phase == RunPhase.PLANNING
    assert [item.phase for item in started.history] == [RunPhase.PLANNING]

    coordinator.schedule_planning(started.run_id)
    await coordinator.drain()
    planned = coordinator.get(started.run_id)

    assert planned is not None
    assert planned.phase == RunPhase.PLAN_READY
    assert [item.phase for item in planned.history] == [RunPhase.PLANNING, RunPhase.PLAN_READY]

    cancelled = coordinator.cancel(started.run_id)
    assert cancelled.history[-1].phase == RunPhase.CANCELLED


@pytest.mark.asyncio
async def test_coordinator_records_blocked_history_with_error_code() -> None:
    coordinator = _coordinator(FailingPlanner(), 8)

    run = await coordinator.create("Fail", RunMode.PLAN_ONLY)

    assert run.phase == RunPhase.BLOCKED
    assert run.history[-1].error_code == "RuntimeError"


def test_phase_transition_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PhaseTransition(phase=RunPhase.PLANNING, at=datetime(2026, 7, 11, 15))  # noqa: DTZ001


def test_run_detail_renders_timeline_from_history(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    created = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "plan_only", "csrf_token": csrf},
        follow_redirects=False,
    )
    detail = client.get(created.headers["location"])

    assert "Run timeline" in detail.text
    assert "Planning" in detail.text
    assert "Plan Ready" in detail.text


@pytest.mark.asyncio
async def test_inspect_audit_run_projects_manifest_events_and_reports(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_directory = await _finalized_dry_run(settings, "c" * 32)

    inspection = inspect_audit_run(run_directory)

    assert inspection.manifest is not None
    assert inspection.manifest.application_version == "0.1.0"
    assert inspection.manifest.artifacts
    assert inspection.events
    assert all(view.event is not None and not view.errors for view in inspection.events)
    assert inspection.stream_errors == ()
    assert inspection.report is None
    assert inspection.dry_run is not None
    assert inspection.raw_report is not None
    assert "preview_ready" in inspection.raw_report


@pytest.mark.asyncio
async def test_inspect_audit_run_attributes_tampered_lines(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_directory = await _finalized_dry_run(settings, "d" * 32)
    stream = run_directory / "run.jsonl"
    lines = stream.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"outcome":"', '"outcome":"tampered-', 1)
    stream.write_text("\n".join(lines) + "\n", encoding="utf-8")

    inspection = inspect_audit_run(run_directory)

    assert inspection.events[0].errors
    assert any("hash" in error for error in inspection.events[0].errors)

    (run_directory / "manifest.json").write_text("{broken", encoding="utf-8")
    broken = inspect_audit_run(run_directory)
    assert broken.manifest is None

    empty = inspect_audit_run(tmp_path / "missing-run")
    assert empty.events == ()
    assert empty.stream_errors == ("event stream is unavailable",)


@pytest.mark.asyncio
async def test_audit_detail_page_shows_manifest_artifacts_and_timeline(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await _finalized_dry_run(settings, "e" * 32)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    detail = client.get(f"/audits/{'e' * 32}")

    assert detail.status_code == 200
    assert "Environment manifest" in detail.text
    assert "0.1.0" in detail.text
    assert "Event timeline" in detail.text
    assert "Artifacts" in detail.text
    assert "data-copy" in detail.text
    assert "Redacted ZIP" in detail.text
    assert "Raw report JSON" in detail.text


@pytest.mark.asyncio
async def test_console_export_returns_idempotent_redacted_zip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    await _finalized_dry_run(settings, "a" * 32)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    first = client.post(f"/audits/{'a' * 32}/export", data={"csrf_token": csrf})
    second = client.post(f"/audits/{'a' * 32}/export", data={"csrf_token": csrf})
    missing = client.post(f"/audits/{'f' * 32}/export", data={"csrf_token": csrf})

    assert first.status_code == 200
    assert first.headers["content-type"] == "application/zip"
    assert "attachment" in first.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert "export-manifest.json" in archive.namelist()
        assert "manifest.json" in archive.namelist()
    assert second.content == first.content
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_console_export_wraps_write_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    await _finalized_dry_run(settings, "b" * 32)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    def failing_export(_source: Path, _destination: Path) -> Path:
        message = "controlled export failure"
        raise AuditWriteFailure(message)

    monkeypatch.setattr(
        "compliance_agent.console.routes.export_redacted_zip",
        failing_export,
    )
    response = client.post(
        f"/audits/{'b' * 32}/export",
        data={"csrf_token": console.security.csrf_token()},
    )

    assert response.status_code == 500
    assert "Export failed" in response.text


@pytest.mark.asyncio
async def test_ownership_console_service_marks_recoverable_findings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    await _finalized_observation(settings, "9" * 32, state)
    catalog = AuditCatalog(settings.audit_dir)

    evidence = latest_observed_state(catalog)
    assert evidence is not None
    assert evidence.run.run_id == "9" * 32

    findings = health_with_recoverability(evidence, OwnershipRegistry(), "[Compliance Agent]")
    assert findings[0].status == "registry_missing"
    assert findings[0].recoverable_from_audit


@pytest.mark.asyncio
async def test_ownership_page_supports_exact_recovery_flow(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    await _finalized_observation(settings, "8" * 32, state)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()
    phrase = f"RECOVER {SECOND_ID.hex[:8].upper()}"

    page = client.get("/ownership")
    assert "Assessed against audit run" in page.text
    assert "registry missing" in page.text
    assert phrase in page.text

    wrong = client.post(
        f"/ownership/{SECOND_ID}/recover",
        data={"csrf_token": csrf, "evidence_run_id": "8" * 32, "confirmation": "wrong"},
    )
    unknown_run = client.post(
        f"/ownership/{SECOND_ID}/recover",
        data={"csrf_token": csrf, "evidence_run_id": "f" * 32, "confirmation": phrase},
    )
    recovered = client.post(
        f"/ownership/{SECOND_ID}/recover",
        data={"csrf_token": csrf, "evidence_run_id": "8" * 32, "confirmation": phrase},
        follow_redirects=False,
    )

    assert wrong.status_code == 400
    assert "Recovery refused" in wrong.text
    assert unknown_run.status_code == 400
    assert recovered.status_code == 303
    assert OwnershipStore(settings.state_dir).load().find(SECOND_ID) is not None
    assert "healthy" in client.get("/ownership").text


def test_ownership_page_without_evidence_offers_no_recovery(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    page = client.get("/ownership")

    assert "No audited observation available" in page.text
    assert "RECOVER" not in page.text


def test_retention_plan_is_visible_before_any_deletion(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    empty = client.get("/audits")
    assert "No runs are older than" in empty.text
    assert "disabled" in empty.text

    expired = settings.audit_dir / "runs" / f"20000101T000000Z-{'0' * 32}"
    expired.mkdir(parents=True)
    planned = client.get("/audits")
    assert "DELETE 1 RUNS" in planned.text
    assert f"20000101T000000Z-{'0' * 32}" in planned.text

    stale = client.post(
        "/audits/prune",
        data={"csrf_token": csrf, "confirmation": "DELETE 9 RUNS"},
    )
    assert stale.status_code == 400
    assert "DELETE 1 RUNS" in stale.text

    applied = client.post(
        "/audits/prune",
        data={"csrf_token": csrf, "confirmation": "DELETE 1 RUNS"},
        follow_redirects=False,
    )
    assert applied.status_code == 303
    assert not expired.exists()


def test_retention_failure_is_reported_with_styled_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    (settings.audit_dir / "runs" / f"20000101T000000Z-{'0' * 32}").mkdir(parents=True)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    def failing_delete(self: object, _candidates: tuple) -> tuple:
        message = "controlled retention failure"
        raise AuditRetentionFailure(message)

    monkeypatch.setattr(
        "compliance_agent.console.routes.AuditRetentionService.delete_expired",
        failing_delete,
    )
    response = client.post(
        "/audits/prune",
        data={"csrf_token": console.security.csrf_token(), "confirmation": "DELETE 1 RUNS"},
    )

    assert response.status_code == 500
    assert "Retention failed" in response.text


def test_greeting_and_readiness_cache_are_deterministic(tmp_path: Path) -> None:
    assert greeting_for_hour(6) == "Good morning"
    assert greeting_for_hour(13) == "Good afternoon"
    assert greeting_for_hour(22) == "Good evening"
    assert greeting_for_hour(2) == "Good evening"

    clock = SettableClock(NOW)
    cache = ReadinessCache(_settings(tmp_path), clock, ttl_seconds=30)
    first = cache.health()
    assert first.blocking_count >= 1  # no accepted UI contract pack exists
    assert cache.health() is first
    clock.moment = NOW + timedelta(seconds=31)
    assert cache.health() is not first


def test_dashboard_shows_real_greeting_and_health_badge(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    dashboard = client.get("/")

    assert "Good " in dashboard.text
    assert "need attention" in dashboard.text


def test_enhancement_static_assets_are_served(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    for asset in ("theme.js", "tables.js", "console.js", "styles.css"):
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200

    for page in ("/audits", "/ownership", "/propagation"):
        assert "data-enhance" in client.get(page).text
