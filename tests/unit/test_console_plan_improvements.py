"""Regression tests for the substantial operator-console improvement plan."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog, AuditRunSummary
from compliance_agent.application.planning_service import direct_add_plan
from compliance_agent.application.ui_contract_service import contract_pack_digest
from compliance_agent.composition import AcceptedReadAdapters
from compliance_agent.console import create_console_app, routes
from compliance_agent.console.capabilities import ConsoleCapabilities, resolve_capabilities
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
)
from compliance_agent.console.journal import ConsoleRunJournal
from compliance_agent.console.setup_flow import build_setup_steps
from compliance_agent.console.timeline import build_timeline
from compliance_agent.schemas.operations import (
    ConsoleRun,
    PhaseTransition,
    RunMode,
    RunPhase,
    UiContractPack,
)
from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.settings import Settings

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


class StaticPlanner:
    async def create_plan(self, _request_text: str):
        return _plan()


class FixedIdentifiers:
    def __init__(self) -> None:
        self._next = 1

    def new(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class ReadyPreflight:
    async def check(self) -> PreflightResult:
        return PreflightResult(
            status="ready",
            identity=PreflightIdentity(
                administrator_email="admin@example.com",
                workspace_domain="example.com",
            ),
        )


class StaticReader:
    async def read_state(self) -> BlockedSenderState:
        return BlockedSenderState()


class EntryPoint:
    def __init__(self, provider) -> None:
        self._provider = provider

    def load(self):
        if isinstance(self._provider, Exception):
            raise self._provider
        return self._provider


def _settings(tmp_path: Path, mode: RunMode = RunMode.PLAN_ONLY) -> Settings:
    return Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        run_mode=mode,
        expected_admin_email="admin@example.com",
        expected_workspace_domain="example.com",
    )


def _plan():
    return direct_add_plan((AddressEntry(kind="domain", value="new.example"),), "Rejected.")


def _run(phase: RunPhase, *, identifier: int = 1) -> ConsoleRun:
    return ConsoleRun(
        run_id=UUID(int=identifier).hex,
        request_text="Block new.example",
        mode=RunMode.LIVE,
        phase=phase,
        created_at=NOW,
        updated_at=NOW,
        plan=_plan(),
        history=(PhaseTransition(phase=phase, at=NOW),),
    )


def _connect(console) -> TestClient:
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")
    response = client.post(
        "/bootstrap",
        data={"token": console.security.launch_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def _pack(status: str = "read_live_validated", *, contract_id: UUID | None = None):
    values = {
        "contract_id": contract_id or uuid4(),
        "created_at": NOW,
        "status": status,
        "fixture_hashes": ("1" * 64,),
        "contract_names": ("blocked_senders_root",),
    }
    if status != "accepted":
        return UiContractPack(**values)
    unsigned = UiContractPack.model_construct(**values, accepted_digest=None)
    return UiContractPack(**values, accepted_digest=contract_pack_digest(unsigned))


def _write_pack(settings: Settings, pack: UiContractPack) -> None:
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    (settings.state_dir / "ui-contract-pack.json").write_text(
        pack.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _coordinator() -> ConsoleCoordinator:
    return ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=StaticPlanner(),
            identifiers=FixedIdentifiers(),
            clock=lambda: NOW,
            approval_service=ApprovalService(300),
        )
    )


@pytest.mark.asyncio
async def test_coordinator_update_wait_is_event_driven_and_has_heartbeat_timeout() -> None:
    coordinator = _coordinator()
    run = coordinator.create_from_plan("Block new.example", RunMode.PLAN_ONLY, _plan())
    waiter = asyncio.create_task(coordinator.wait_for_update(run.run_id, timeout=30))
    await asyncio.sleep(0)

    coordinator.cancel(run.run_id)

    assert await asyncio.wait_for(waiter, timeout=0.2)
    assert not await coordinator.wait_for_update(run.run_id, timeout=0.001)


@pytest.mark.parametrize(
    ("phase", "restored_phase", "error_code"),
    [
        (RunPhase.PLANNING, RunPhase.BLOCKED, "console_restarted"),
        (RunPhase.PREFLIGHT, RunPhase.BLOCKED, "console_restarted"),
        (RunPhase.AWAITING_APPROVAL, RunPhase.PLAN_READY, "approval_expired"),
        (
            RunPhase.EXECUTING,
            RunPhase.INTERRUPTED,
            "console_restarted_execution_uncertain",
        ),
        (
            RunPhase.VERIFYING,
            RunPhase.INTERRUPTED,
            "console_restarted_execution_uncertain",
        ),
        (RunPhase.PREVIEW_READY, RunPhase.PREVIEW_READY, None),
    ],
)
def test_journal_restore_downgrade_matrix(
    tmp_path: Path,
    phase: RunPhase,
    restored_phase: RunPhase,
    error_code: str | None,
) -> None:
    journal = ConsoleRunJournal(tmp_path)
    journal.save((_run(phase),))

    restored = journal.load(NOW + timedelta(minutes=1))[0]

    assert restored.phase == restored_phase
    assert restored.error_code == error_code
    if error_code:
        assert restored.history[-1].error_code == error_code


def test_journal_corruption_and_unknown_versions_are_left_untouched(tmp_path: Path) -> None:
    path = tmp_path / "console-runs.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    journal = ConsoleRunJournal(tmp_path)

    assert journal.load(NOW) == ()
    journal.save((_run(RunPhase.COMPLETED),))
    assert path.read_text(encoding="utf-8") == "{broken"

    future = [{"schema_version": "2", "run": _run(RunPhase.COMPLETED).model_dump(mode="json")}]
    path.write_text(json.dumps(future), encoding="utf-8")
    journal = ConsoleRunJournal(tmp_path)
    assert journal.load(NOW) == ()
    journal.save(())
    assert json.loads(path.read_text(encoding="utf-8"))[0]["schema_version"] == "2"


def test_console_run_round_trips_across_app_instances(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = create_console_app(settings, planner=StaticPlanner())
    client = _connect(first)
    response = client.post(
        "/runs/direct-add",
        data={
            "target": "persisted.example",
            "target_kind": "domain",
            "notice": "Rejected.",
            "mode": "plan_only",
            "csrf_token": first.security.csrf_token(),
        },
        follow_redirects=False,
    )
    run_path = response.headers["location"]

    second = create_console_app(settings, planner=StaticPlanner())
    restored = _connect(second).get(run_path)

    assert restored.status_code == 200
    assert "persisted.example" in restored.text


def test_htmx_direct_add_validation_and_success_contract(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _connect(console)
    initial_count = len(console.coordinator.list_runs())
    invalid_data = {
        "target": "https://bad.example/*",
        "target_kind": "domain",
        "notice": "echo this safely",
        "mode": "plan_only",
        "csrf_token": console.security.csrf_token(),
    }

    htmx_invalid = client.post(
        "/runs/direct-add",
        data=invalid_data,
        headers={"HX-Request": "true"},
    )
    plain_invalid = client.post("/runs/direct-add", data=invalid_data)

    assert htmx_invalid.status_code == 422
    assert "field-error" in htmx_invalid.text
    assert "echo this safely" in htmx_invalid.text
    assert "<html" not in htmx_invalid.text
    assert plain_invalid.status_code == 400
    assert "Block a sender" in plain_invalid.text
    assert len(console.coordinator.list_runs()) == initial_count

    valid_data = {**invalid_data, "target": "valid.example"}
    created = client.post(
        "/runs/direct-add",
        data=valid_data,
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 204
    assert created.headers["HX-Redirect"].startswith("/runs/")

    invalid_csrf = client.post(
        "/runs/direct-add",
        data={**valid_data, "csrf_token": "wrong"},
        headers={"HX-Request": "true"},
    )
    assert invalid_csrf.status_code == 403
    assert "<html" in invalid_csrf.text


def test_identity_validation_toasts_and_authenticated_partials(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    anonymous = TestClient(console.app, base_url="http://127.0.0.1:8765")
    assert anonymous.get("/partials/session-runs", follow_redirects=False).status_code == 303
    client = _connect(console)

    invalid = client.post(
        "/setup/google-identities",
        data={
            "administrator_email": "not-an-email",
            "workspace_domain": "https://bad.example",
            "csrf_token": console.security.csrf_token(),
        },
        headers={"HX-Request": "true"},
    )
    assert invalid.status_code == 422
    assert invalid.text.count("field-error") >= 2
    assert client.get("/partials/session-runs").status_code == 200
    assert 'data-toast data-tone="success"' in client.get("/?notice=google_identities_saved").text
    assert "data-toast" not in client.get("/?notice=request-controlled").text


def test_activity_pagination_filters_and_partial_auth(tmp_path: Path, monkeypatch) -> None:
    summaries = tuple(
        AuditRunSummary(
            run_id=UUID(int=index + 1).hex,
            run_directory=tmp_path / str(index),
            started_at=NOW + timedelta(minutes=index),
            ended_at=NOW + timedelta(minutes=index + 1),
            status=(RunStatus.NO_CHANGE_REQUIRED if index % 2 else RunStatus.FAILED_UNCHANGED),
            integrity_valid=True,
        )
        for index in range(5)
    )
    monkeypatch.setattr(AuditCatalog, "list_runs", lambda _self: summaries)
    monkeypatch.setattr(routes, "_AUDIT_PAGE_SIZE", 2)
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _connect(console)

    clamped = client.get("/activity?audit_page=999")
    second = client.get("/partials/audit-rows?page=2")
    filtered = client.get("/partials/audit-rows?page=1&status=no_change_required")
    unknown = client.get("/activity?status=not-a-status")

    assert clamped.status_code == 200
    assert sum(summary.run_id[:12] in clamped.text for summary in summaries) == 5
    assert second.text.count("<tr data-status=") == 2
    assert "page=3" in second.text
    assert "Failed unchanged" not in filtered.text
    assert unknown.status_code == 200


def test_setup_steps_dashboard_hero_and_exactly_one_primary_cta(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RunMode.DRY_RUN)
    settings.expected_admin_email = ""
    settings.expected_workspace_domain = ""
    steps = build_setup_steps(settings)

    assert [step.title for step in steps] == [
        "Mode",
        "Storage",
        "Google identities",
        "Admin interface evidence",
        "Browser-backed capability",
    ]
    assert next(step for step in steps if step.state == "current").number == 3

    console = create_console_app(settings, planner=StaticPlanner())
    client = _connect(console)
    setup = client.get("/setup")
    dashboard = client.get("/")

    assert setup.text.count('class="button primary"') == 1
    assert "Finish setting up — step 3 of 5" in dashboard.text

    plan_steps = build_setup_steps(_settings(tmp_path / "plan"))
    assert all(step.state == "not_applicable" for step in plan_steps[2:])


def test_timeline_durations_tones_and_interrupted_guidance(tmp_path: Path) -> None:
    interrupted = _run(RunPhase.INTERRUPTED).model_copy(
        update={
            "source_run_id": UUID(int=9).hex,
            "history": (
                PhaseTransition(phase=RunPhase.PLANNING, at=NOW),
                PhaseTransition(phase=RunPhase.EXECUTING, at=NOW + timedelta(seconds=65)),
                PhaseTransition(
                    phase=RunPhase.INTERRUPTED,
                    at=NOW + timedelta(minutes=2),
                    error_code="outcome_unknown",
                ),
            ),
        }
    )
    timeline = build_timeline(interrupted, NOW + timedelta(hours=1, minutes=2))
    assert timeline[0].duration_label == "1m 05s"
    assert timeline[-1].duration_label == "1h 00m"
    assert timeline[-1].tone == "uncertain"

    settings = _settings(tmp_path)
    ConsoleRunJournal(settings.state_dir).save((interrupted,))
    console = create_console_app(settings, planner=StaticPlanner())
    client = _connect(console)
    detail = client.get(f"/runs/{interrupted.run_id}")
    events = client.get(f"/runs/{interrupted.run_id}/events")

    assert "Do not retry" in detail.text
    assert f"/audits/{interrupted.source_run_id}" in detail.text
    assert "admin.google.com/ac/apps/gmail/safety" in detail.text
    assert "/ownership" in detail.text
    assert "timeline-duration" in events.text


def test_security_reissue_invalidates_old_link_and_preserves_active_session(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())
    client = _connect(console)
    old = console.security.launch_token
    new_url = console.security.reissue_bootstrap_url()
    new = new_url.rsplit("#", maxsplit=1)[1]

    assert client.get("/").status_code == 200
    assert client.post("/bootstrap", data={"token": old}).status_code == 403
    assert client.get("/").status_code == 200
    assert client.post("/bootstrap", data={"token": new}, follow_redirects=False).status_code == 303


@pytest.mark.asyncio
async def test_capability_discovery_preview_and_fail_closed_branches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path, RunMode.DRY_RUN)
    pack = _pack()
    _write_pack(settings, pack)

    monkeypatch.setattr(
        "compliance_agent.console.capabilities.importlib.metadata.entry_points",
        lambda **_kwargs: (),
    )
    assert resolve_capabilities(settings).unavailable_reason == "adapters_not_installed"

    adapters = AcceptedReadAdapters(
        preflight=ReadyPreflight(),
        current_reader=StaticReader(),
        contract_pack=pack,
    )
    monkeypatch.setattr(
        "compliance_agent.console.capabilities.importlib.metadata.entry_points",
        lambda **_kwargs: (EntryPoint(lambda _settings, _pack: adapters),),
    )
    available = resolve_capabilities(settings)
    assert available.preview_service is not None
    result = await available.preview_service.preview(_plan(), "Block new.example")
    assert result.status == "preview_ready"
    assert tuple((settings.audit_dir / "runs").iterdir())

    mismatched = AcceptedReadAdapters(
        preflight=ReadyPreflight(),
        current_reader=StaticReader(),
        contract_pack=_pack(contract_id=uuid4()),
    )
    monkeypatch.setattr(
        "compliance_agent.console.capabilities.importlib.metadata.entry_points",
        lambda **_kwargs: (EntryPoint(lambda _settings, _pack: mismatched),),
    )
    assert resolve_capabilities(settings).unavailable_reason == "adapter_contract_digest_mismatch"

    monkeypatch.setattr(
        "compliance_agent.console.capabilities.importlib.metadata.entry_points",
        lambda **_kwargs: (EntryPoint(RuntimeError("provider failed")),),
    )
    assert resolve_capabilities(settings).unavailable_reason == "adapter_provider_failed"


def test_readiness_reflects_injected_browser_capability(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RunMode.DRY_RUN)
    pack = _pack()
    _write_pack(settings, pack)
    capabilities = ConsoleCapabilities(
        preview_service=SimpleNamespace(),
        contract_status=pack.status,
    )

    steps = build_setup_steps(settings, capabilities)

    assert steps[-1].state == "complete"
