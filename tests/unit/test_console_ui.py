"""Console UX enhancements: live updates, styled errors, audit depth, recovery."""

import asyncio
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.audit_inspection_service import inspect_audit_run
from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.application.change_presentation import address_list_deltas
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.dry_run_audit_service import DryRunAuditFinalizationService
from compliance_agent.application.dry_run_service import DryRunDependencies, DryRunService
from compliance_agent.application.ownership_console_service import (
    health_with_recoverability,
    latest_observed_state,
)
from compliance_agent.application.planning_service import direct_add_plan
from compliance_agent.application.workflow_audit_service import (
    PreparedChangeAudit,
    WorkflowAuditService,
)
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.console import create_console_app
from compliance_agent.console.app import ConsoleApplication
from compliance_agent.console.configuration import LocalConfigurationStore
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
)
from compliance_agent.console.notices import resolve_notice
from compliance_agent.console.readiness import ReadinessCache, collect_readiness, greeting_for_hour
from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import AuditRetentionFailure, AuditWriteFailure, PlannerFailure
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.schemas.changes import DesiredStateResult
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


class UnavailablePlanner:
    async def create_plan(self, _request_text: str) -> None:
        message = "Ollama is unavailable"
        raise PlannerFailure(message)


class DelayedPlanner:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def create_plan(self, _request_text: str):
        self.started.set()
        await self.release.wait()
        return direct_add_plan((domain("race.example"),), None)


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
    *,
    proves_creation: bool = False,
) -> Path:
    """Finalize a run that recorded an after-state observation."""

    run_directory = settings.audit_dir / "runs" / f"20260711T150000Z-{run_id}"
    writer = RunAuditWriter(run_directory)
    auditor = WorkflowAuditService(writer, FixedClock(), run_id)
    status = RunStatus.NO_CHANGE_REQUIRED
    if proves_creation:
        plan = direct_add_plan(state.address_lists[0].entries, state.rules[0].rejection_notice)
        before = BlockedSenderState()
        desired = DesiredStateResult(desired_state=state)
        change_set = calculate_change_set(before, state)
        auditor.record_prepared_change(
            PreparedChangeAudit(
                plan=plan,
                current_state=before,
                desired=desired,
                change_set=change_set,
                plan_hash=canonical_hash(plan),
                before_state_hash=canonical_hash(before),
                change_set_hash=canonical_hash(change_set),
            )
        )
        status = RunStatus.APPLIED_UI_VERIFIED
    auditor.record_state("after", state)
    await AuditFinalizationService(writer, FixedClock(), run_id, _metadata()).finalize(
        RunResult(status=status)
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
    assert "Session expired" in forbidden.text
    assert "earlier console session" in forbidden.text
    assert "No mutation was authorized" not in forbidden.text
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
async def test_cancelled_background_plan_cannot_resurrect_run() -> None:
    planner = DelayedPlanner()
    coordinator = _coordinator(planner, 70)
    started = coordinator.start("Block race.example", RunMode.PLAN_ONLY)

    coordinator.schedule_planning(started.run_id)
    await planner.started.wait()
    cancelled = coordinator.cancel(started.run_id)
    planner.release.set()
    await coordinator.drain()

    final = coordinator.get(started.run_id)
    assert final == cancelled
    assert final is not None
    assert [item.phase for item in final.history] == [RunPhase.PLANNING, RunPhase.CANCELLED]


@pytest.mark.asyncio
async def test_coordinator_records_blocked_history_with_error_code() -> None:
    coordinator = _coordinator(FailingPlanner(), 8)

    run = await coordinator.create("Fail", RunMode.PLAN_ONLY)

    assert run.phase == RunPhase.BLOCKED
    assert run.history[-1].error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_planner_failure_renders_actionable_no_ollama_recovery(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=UnavailablePlanner())
    client = _client(console)
    run = await console.coordinator.create("Block new.example", RunMode.PLAN_ONLY)

    detail = client.get(f"/runs/{run.run_id}")

    assert run.error_code == "planner_unavailable"
    assert "created with local AI" in detail.text
    assert "Your Google account settings are not the problem" in detail.text
    assert "Use the built-in form" in detail.text
    assert 'value="new.example"' in detail.text
    assert "Create plan without AI" in detail.text
    assert "No mutation was authorized" not in detail.text


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
    await _finalized_observation(settings, "9" * 32, state, proves_creation=True)
    catalog = AuditCatalog(settings.audit_dir)

    evidence = latest_observed_state(catalog)
    assert evidence is not None
    assert evidence.run.run_id == "9" * 32

    findings = health_with_recoverability(evidence, OwnershipRegistry(), "[Compliance Agent]")
    assert findings[0].status == "registry_missing"
    assert findings[0].recoverable_from_audit


@pytest.mark.asyncio
async def test_read_only_observation_cannot_establish_recovery_provenance(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    await _finalized_observation(settings, "7" * 32, state)

    evidence = latest_observed_state(AuditCatalog(settings.audit_dir))
    assert evidence is not None
    findings = health_with_recoverability(evidence, OwnershipRegistry(), "[Compliance Agent]")

    assert findings[0].status == "registry_missing"
    assert not findings[0].recoverable_from_audit


@pytest.mark.asyncio
async def test_audit_catalog_ignores_integrity_valid_run_with_malformed_directory_name(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    run_directory = await _finalized_observation(settings, "6" * 32, state)
    run_directory.rename(run_directory.parent / "malformed-run-name")

    assert AuditCatalog(settings.audit_dir).list_runs() == ()


@pytest.mark.asyncio
async def test_ownership_page_supports_exact_recovery_flow(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    await _finalized_observation(settings, "8" * 32, state, proves_creation=True)
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
    assert first.blocking_count == 0  # Google Admin setup is optional in plan-only mode
    assert cache.health() is first
    cache.invalidate()
    assert cache.health() is not first
    first = cache.health()
    clock.moment = NOW + timedelta(seconds=31)
    assert cache.health() is not first


def test_dashboard_leads_with_the_primary_task_and_capability_limit(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    dashboard = client.get("/")

    assert "Draft sender-blocking plans" in dashboard.text
    assert "Planning only" in dashboard.text
    assert "No Google changes" in dashboard.text
    assert "Choose what happens next" in dashboard.text
    assert "Enable safe preview or live apply in Settings" in dashboard.text
    assert "needs attention" not in dashboard.text


def test_invalid_contract_evidence_fails_closed_without_taking_down_console(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.state_dir.mkdir(parents=True)
    (settings.state_dir / "ui-contract-pack.json").write_text("{broken", encoding="utf-8")
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    dashboard = client.get("/")
    settings_page = client.get("/setup")
    contracts = client.get("/contracts")
    readiness = client.get("/readiness")

    assert dashboard.status_code == 200
    assert "Draft sender-blocking plans" in dashboard.text
    assert "Installed interface evidence is invalid" in settings_page.text
    assert contracts.status_code == 200
    assert "Invalid evidence" in contracts.text
    assert readiness.status_code == 200
    assert "invalid" in readiness.text


def test_enhancement_static_assets_are_served(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    assets = (
        "theme.js",
        "tables.js",
        "console.js",
        "styles.css",
        "favicon.svg",
        "relative-time.js",
    )
    for asset in assets:
        response = client.get(f"/static/{asset}")
        assert response.status_code == 200

    for page in ("/audits", "/ownership", "/propagation"):
        assert "data-enhance" in client.get(page).text


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"notice": "ownership_recovered"}, "Ownership record recovered from audited evidence."),
        (
            {"notice": "google_identities_saved"},
            "Expected Google account saved. This verifies a future session; "
            "it does not enable Google Admin integration.",
        ),
        (
            {"notice": "run_mode_saved"},
            "Run mode saved. New runs now use the selected capability level.",
        ),
        ({"notice": "retention_applied", "count": "1"}, "Retention applied — 1 audit run deleted."),
        (
            {"notice": "retention_applied", "count": "2"},
            "Retention applied — 2 audit runs deleted.",
        ),
        ({"notice": "retention_applied"}, None),
        ({"notice": "retention_applied", "count": "many"}, None),
        ({"notice": "retention_applied", "count": "-1"}, None),
        ({"notice": "retention_applied", "count": "999999999"}, None),
        ({"notice": "unknown_key"}, None),
        ({}, None),
    ],
)
def test_resolve_notice_is_allow_listed(params: dict[str, str], expected: str | None) -> None:
    assert resolve_notice(params) == expected


def test_notice_banner_renders_for_allow_listed_keys_only(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    shown = client.get("/audits?notice=retention_applied&count=2")
    ignored = client.get("/audits?notice=totally_unknown")

    assert "notice-banner" in shown.text
    assert "2 audit runs deleted" in shown.text
    assert "data-dismiss" in shown.text
    assert "notice-banner" not in ignored.text


def test_prune_and_recover_redirects_carry_notice_params(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (settings.audit_dir / "runs" / f"20000101T000000Z-{'0' * 32}").mkdir(parents=True)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    applied = client.post(
        "/audits/prune",
        data={"csrf_token": console.security.csrf_token(), "confirmation": "DELETE 1 RUNS"},
        follow_redirects=False,
    )

    assert applied.status_code == 303
    assert applied.headers["location"] == "/audits?notice=retention_applied&count=1"
    landed = client.get(applied.headers["location"])
    assert "1 audit run deleted" in landed.text


@pytest.mark.asyncio
async def test_recovery_redirect_lands_on_success_banner(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    await _finalized_observation(settings, "5" * 32, state, proves_creation=True)
    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)

    recovered = client.post(
        f"/ownership/{SECOND_ID}/recover",
        data={
            "csrf_token": console.security.csrf_token(),
            "evidence_run_id": "5" * 32,
            "confirmation": f"RECOVER {SECOND_ID.hex[:8].upper()}",
        },
        follow_redirects=False,
    )

    assert recovered.status_code == 303
    assert recovered.headers["location"] == "/ownership?notice=ownership_recovered"
    landed = client.get(recovered.headers["location"])
    assert "Ownership record recovered" in landed.text


def test_run_detail_renders_plan_summary_cards(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    created = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "plan_only", "csrf_token": csrf},
        follow_redirects=False,
    )
    detail = client.get(created.headers["location"])

    assert "Validated actions" in detail.text
    assert "Block 1 sender" in detail.text
    assert "entry-chip" in detail.text
    assert "Nothing was previewed or applied" in detail.text
    assert "Plan complete" in detail.text
    assert "Enable preview or live apply" in detail.text
    assert "Draft another plan" in detail.text
    assert "Raw plan JSON" in detail.text
    assert "add_blocked_entries" in detail.text  # regression guard for the mega-test


def test_change_summary_macro_renders_created_and_updated_resources() -> None:
    before = owned_state(ownership_id=SECOND_ID, entries=(domain("keep.example"),))
    after_list = before.address_lists[0].model_copy(
        update={"entries": (domain("keep.example"), domain("new.example"))}
    )
    after = before.model_copy(update={"address_lists": (after_list,)})
    change_set = calculate_change_set(before, after)
    deltas = address_list_deltas(change_set)

    templates = Jinja2Templates(directory=Path("src/compliance_agent/console/templates"))
    module = templates.env.get_template("partials/_change_summary.html").module
    html = str(module.summary(change_set, deltas))

    assert "Address list changes" in html
    assert "change-row update" in html
    assert "new.example" in html
    assert "delta-line added" in html


def test_address_list_deltas_computes_added_and_removed() -> None:
    before = owned_state(
        ownership_id=SECOND_ID,
        entries=(domain("old.example"), domain("keep.example")),
    )
    updated_list = before.address_lists[0].model_copy(
        update={"entries": (domain("keep.example"), domain("new.example"))}
    )
    change_set = calculate_change_set(
        before,
        before.model_copy(update={"address_lists": (updated_list,)}),
    )

    deltas = address_list_deltas(change_set)
    delta = deltas[SECOND_ID.hex]

    assert [entry.value for entry in delta.added] == ["new.example"]
    assert [entry.value for entry in delta.removed] == ["old.example"]
    assert address_list_deltas(calculate_change_set(before, before)) == {}


def test_topbar_explains_run_capability_on_every_page(tmp_path: Path) -> None:
    plan_only = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(plan_only)
    dashboard = client.get("/")

    assert "topbar-capability" in dashboard.text
    assert "Planning only" in dashboard.text
    assert "No Google changes" in dashboard.text

    dry_run = create_console_app(
        _settings(tmp_path / "dry", RunMode.DRY_RUN),
        planner=StaticPlanner(),
    )
    dry_client = _client(dry_run)
    assert "Preview only" in dry_client.get("/audits").text


def test_bootstrap_page_is_theme_aware_and_shows_mode(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")

    page = client.get("/bootstrap")

    assert page.status_code == 200
    assert "theme.js" in page.text
    assert "icon-check" in page.text
    assert "mode-chip" in page.text
    assert "Opening your local console" in page.text
    assert "Start-Gmail-Agent.cmd" in page.text
    assert 'id="bootstrap-form" hidden' in page.text
    assert "works exactly once" in page.text
    assert "ad•••@example.com" not in page.text


def test_empty_states_offer_guided_next_steps(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    audits = client.get("/audits")
    ownership = client.get("/ownership")
    propagation = client.get("/propagation")

    assert "empty-cell" in audits.text
    assert 'href="/runs/new"' in audits.text
    assert 'href="/audits"' in ownership.text
    assert "after a verified live apply" in propagation.text


def test_new_change_leads_with_deterministic_path_and_fixed_launch_mode(
    tmp_path: Path,
) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)

    page = client.get("/runs/new")

    assert page.text.index("Sender details") < page.text.index("Use local AI instead")
    assert "Plan creation works immediately without local AI or Google access" in page.text
    assert "This mode stops after the reviewed plan" in page.text
    assert 'name="mode"' not in page.text


def test_readiness_items_expose_hints_and_actions(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        expected_admin_email="",
        expected_workspace_domain="example.com",
    )
    items = {item.name: item for item in collect_readiness(settings)}

    assert items["Administrator identity"].code_hint == "CA_EXPECTED_ADMIN_EMAIL"
    assert items["Workspace identity"].code_hint is None
    assert not items["Administrator identity"].blocking
    assert items["Administrator identity"].action_href == "/setup#google-account"
    assert items["Google Admin interface evidence"].action_href == "/contracts"

    console = create_console_app(settings, planner=StaticPlanner())
    client = _client(console)
    page = client.get("/readiness")

    assert "env-hint" in page.text
    assert "CA_EXPECTED_ADMIN_EMAIL" in page.text
    assert 'href="/contracts"' in page.text
    assert 'href="/setup#google-account"' in page.text
    assert "Planning diagnostics passed" in page.text


def test_browser_backed_readiness_requires_identity_and_contract(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        run_mode=RunMode.DRY_RUN,
        expected_admin_email="",
        expected_workspace_domain="",
    )

    items = {item.name: item for item in collect_readiness(settings)}

    assert items["Administrator identity"].blocking
    assert items["Workspace identity"].blocking
    assert items["Google Admin interface evidence"].blocking
    assert items["Browser-backed capability"].blocking
    assert items["Browser-backed capability"].status == "not_installed"


def test_readiness_blocks_an_existing_inaccessible_storage_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    settings.state_dir.mkdir(parents=True)
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == settings.state_dir:
            message = "denied"
            raise PermissionError(message)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    items = {item.name: item for item in collect_readiness(settings)}

    state = items["State storage"]
    assert state.status == "inaccessible"
    assert state.blocking
    assert state.code_hint == "CA_STATE_DIR"
    assert "cannot access" in state.detail


def test_setup_page_saves_validated_google_identities(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        expected_admin_email="",
        expected_workspace_domain="",
    )
    configuration_file = tmp_path / ".env"
    configuration_file.write_text(
        "# CA_EXPECTED_ADMIN_EMAIL=admin@example.com\n"
        "# CA_EXPECTED_WORKSPACE_DOMAIN=example.com\n"
        "CA_CONSOLE_OPEN_BROWSER=true\n",
        encoding="utf-8",
    )
    console = create_console_app(
        settings,
        planner=StaticPlanner(),
        configuration_file=configuration_file,
    )
    client = _client(console)

    saved = client.post(
        "/setup/google-identities",
        data={
            "csrf_token": console.security.csrf_token(),
            "administrator_email": " Admin@Example.COM ",
            "workspace_domain": "Example.COM",
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == "/setup?notice=google_identities_saved#google-account"
    content = configuration_file.read_text(encoding="utf-8")
    assert "CA_EXPECTED_ADMIN_EMAIL=admin@example.com" in content
    assert "CA_EXPECTED_WORKSPACE_DOMAIN=example.com" in content
    assert "CA_CONSOLE_OPEN_BROWSER=true" in content
    assert settings.expected_admin_email == "admin@example.com"
    assert settings.expected_workspace_domain == "example.com"
    landed = client.get(saved.headers["location"])
    assert "Expected Google account saved" in landed.text
    assert "does not enable Google Admin integration" in landed.text
    assert "Currently ad•••@example.com" in landed.text


def test_setup_page_persists_mode_and_server_owns_new_run_mode(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configuration_file = tmp_path / ".env"
    configuration_file.write_text(
        "CA_PLAN_ONLY=true\nCA_DRY_RUN=true\nCA_CONSOLE_OPEN_BROWSER=true\n",
        encoding="utf-8",
    )
    console = create_console_app(
        settings,
        planner=StaticPlanner(),
        configuration_file=configuration_file,
    )
    client = _client(console)
    csrf = console.security.csrf_token()

    saved = client.post(
        "/setup/run-mode",
        data={"csrf_token": csrf, "run_mode": "dry_run"},
        follow_redirects=False,
    )

    assert saved.status_code == 303
    assert saved.headers["location"] == "/setup?notice=run_mode_saved#run-mode"
    content = configuration_file.read_text(encoding="utf-8")
    assert "CA_RUN_MODE=dry_run" in content
    assert "CA_PLAN_ONLY" not in content
    assert "CA_DRY_RUN" not in content
    assert settings.run_mode == RunMode.DRY_RUN
    assert "Preview a sender block" in client.get("/runs/new").text
    assert 'name="mode"' not in client.get("/runs/new").text

    created = client.post(
        "/runs/direct-add",
        data={
            "csrf_token": csrf,
            "target": "server-owned.example",
            "target_kind": "domain",
            "mode": "live",
        },
        follow_redirects=False,
    )
    run_id = created.headers["location"].rsplit("/", 1)[-1]
    run = console.coordinator.get(run_id)
    assert run is not None
    assert run.mode == RunMode.DRY_RUN
    unavailable = client.post(f"/runs/{run_id}/preview", data={"csrf_token": csrf})
    assert unavailable.status_code == 409
    assert console.coordinator.get(run_id).phase == RunPhase.PLAN_READY  # type: ignore[union-attr]

    live = client.post(
        "/setup/run-mode",
        data={"csrf_token": csrf, "run_mode": "live"},
        follow_redirects=False,
    )
    assert live.status_code == 303
    assert settings.run_mode == RunMode.LIVE
    assert "Live mode · Execution locked" in client.get("/").text
    locked_apply = client.post(
        f"/runs/{run_id}/approve",
        data={
            "csrf_token": csrf,
            "phrase": "APPLY",
            "acknowledged": "true",
        },
    )
    assert locked_apply.status_code == 409
    assert "Live execution is locked" in locked_apply.text
    assert "supervised Google Admin interface evidence" in locked_apply.text

    invalid = client.post(
        "/setup/run-mode",
        data={"csrf_token": csrf, "run_mode": "invented"},
    )
    assert invalid.status_code == 400
    assert "Choose one available run mode" in invalid.text


def test_mode_change_rejects_headless_and_active_browser_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    settings.headless = True
    console = create_console_app(
        settings,
        planner=StaticPlanner(),
        configuration_file=tmp_path / ".env",
    )
    client = _client(console)
    csrf = console.security.csrf_token()

    headless = client.post(
        "/setup/run-mode",
        data={"csrf_token": csrf, "run_mode": "live"},
    )
    assert headless.status_code == 400
    assert "Live mode requires a visible browser" in headless.text

    settings.headless = False
    run = console.coordinator.create_from_plan(
        "Block active.example",
        RunMode.DRY_RUN,
        direct_add_plan((domain("active.example"),), None),
    )
    monkeypatch.setattr(console.coordinator, "active_browser_run", lambda: run)
    active = client.post(
        "/setup/run-mode",
        data={"csrf_token": csrf, "run_mode": "dry_run"},
    )
    assert active.status_code == 400
    assert run.run_id[:8].upper() in active.text


def test_live_mode_requires_expected_identities(tmp_path: Path) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        expected_admin_email="",
        expected_workspace_domain="",
    )
    console = create_console_app(
        settings,
        planner=StaticPlanner(),
        configuration_file=tmp_path / ".env",
    )
    client = _client(console)

    refused = client.post(
        "/setup/run-mode",
        data={"csrf_token": console.security.csrf_token(), "run_mode": "live"},
    )

    assert refused.status_code == 400
    assert "Configure the expected administrator" in refused.text
    assert settings.run_mode == RunMode.PLAN_ONLY


def test_completed_plan_can_adopt_a_browser_backed_mode_before_preview() -> None:
    coordinator = _coordinator(StaticPlanner(), 81)
    plan = direct_add_plan((domain("continue.example"),), None)
    run = coordinator.create_from_plan("Block continue.example", RunMode.PLAN_ONLY, plan)

    promoted = coordinator.promote_plan(run.run_id, RunMode.DRY_RUN)

    assert promoted.mode == RunMode.DRY_RUN
    assert promoted.phase == RunPhase.PLAN_READY
    promoted_again = coordinator.promote_plan(run.run_id, RunMode.LIVE)
    assert promoted_again.mode == RunMode.LIVE
    assert coordinator.promote_plan(run.run_id, RunMode.LIVE) is promoted_again
    with pytest.raises(ValueError, match="not eligible"):
        coordinator.promote_plan(run.run_id, RunMode.PLAN_ONLY)
    planning = coordinator.start("Block pending.example", RunMode.DRY_RUN)
    with pytest.raises(ValueError, match="only a completed plan"):
        coordinator.promote_plan(planning.run_id, RunMode.LIVE)


def test_local_configuration_store_collapses_duplicates_and_rejects_other_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "CA_EXPECTED_ADMIN_EMAIL=old@example.com\n"
        "CA_EXPECTED_ADMIN_EMAIL=duplicate@example.com\n"
        "# CA_EXPECTED_WORKSPACE_DOMAIN=old.example\n"
        "OTHER_SETTING=preserved\n",
        encoding="utf-8",
    )
    store = LocalConfigurationStore(path)

    email, domain = store.save_google_identities("Admin@Example.com", "Example.com")

    content = path.read_text(encoding="utf-8")
    assert email == "admin@example.com"
    assert domain == "example.com"
    assert content.count("CA_EXPECTED_ADMIN_EMAIL=") == 1
    assert content.count("CA_EXPECTED_WORKSPACE_DOMAIN=") == 1
    assert "OTHER_SETTING=preserved" in content
    store.save_run_mode(RunMode.DRY_RUN)
    content = path.read_text(encoding="utf-8")
    assert "CA_RUN_MODE=dry_run" in content
    models = store.save_agent_models("gemma4:12b", "qwen3-vl:8b")
    assert models == ("gemma4:12b", "qwen3-vl:8b")
    content = path.read_text(encoding="utf-8")
    assert "CA_OLLAMA_MODEL=gemma4:12b" in content
    assert "CA_BROWSER_MODEL=qwen3-vl:8b" in content
    with pytest.raises(ValueError, match="valid local Ollama model tag"):
        store.save_agent_models("bad model", "gemma4:12b")
    with pytest.raises(ValueError, match="cannot be edited"):
        store.update({"CA_HEADLESS": "true"})
    with pytest.raises(ValueError, match="cannot be removed"):
        store.update({}, remove_keys=frozenset({"CA_HEADLESS"}))


def test_timestamps_render_as_relative_time_elements(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _client(console)
    csrf = console.security.csrf_token()

    created = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "plan_only", "csrf_token": csrf},
        follow_redirects=False,
    )
    run_url = created.headers["location"]
    dashboard = client.get("/")
    events = client.get(f"{run_url}/events")

    assert "data-relative" in dashboard.text
    assert 'data-value="20' in dashboard.text  # ISO sort key kept for tables.js
    assert "data-relative" in events.text  # macro renders in the standalone SSE fragment
