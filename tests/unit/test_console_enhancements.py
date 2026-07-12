"""Safe console, dry-run, approval, contract, recovery, and propagation behavior."""

import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.dry_run_audit_service import DryRunAuditFinalizationService
from compliance_agent.application.dry_run_service import DryRunDependencies, DryRunService
from compliance_agent.application.fixture_inspection_service import inspect_fixture_directory
from compliance_agent.application.impact_service import assess_impact
from compliance_agent.application.ownership_health_service import assess_ownership_health
from compliance_agent.application.ownership_recovery_service import OwnershipRecoveryService
from compliance_agent.application.planning_service import direct_add_plan, direct_list_plan
from compliance_agent.application.propagation_service import PropagationService
from compliance_agent.application.ui_contract_service import (
    UiContractStore,
    contract_pack_digest,
)
from compliance_agent.application.workflow_audit_service import WorkflowAuditService
from compliance_agent.audit.export import export_redacted_zip
from compliance_agent.audit.manifest import RunManifestMetadata
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.composition import AcceptedReadAdapters, compose_dry_run_runtime
from compliance_agent.console import create_console_app
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
)
from compliance_agent.console.readiness import mask_identity
from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.exceptions import (
    AuditWriteFailure,
    OwnershipNotEstablished,
    UnvalidatedUiContract,
)
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.protected_json import ProtectedJsonStore
from compliance_agent.schemas.changes import DesiredStateResult
from compliance_agent.schemas.operations import (
    DryRunResult,
    PropagationRecord,
    RunMode,
    UiContractPack,
)
from compliance_agent.schemas.preflight import PreflightIdentity, PreflightResult
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.settings import Settings
from tests.conftest import SECOND_ID, domain, owned_state, registry_for

NOW = datetime(2026, 7, 11, 15, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FixedIdentifiers:
    def __init__(self, *values: UUID) -> None:
        self._values = list(values or (SECOND_ID,))

    def new(self) -> UUID:
        return self._values.pop(0) if self._values else SECOND_ID


class ReadyPreflight:
    async def check(self) -> PreflightResult:
        return PreflightResult(
            status="ready",
            identity=PreflightIdentity(
                administrator_email="admin@example.com",
                workspace_domain="example.com",
            ),
        )


class FailedPreflight:
    async def check(self) -> PreflightResult:
        return PreflightResult(status="failed", reason_code="selector_not_found")


class StaticReader:
    def __init__(self, state: BlockedSenderState | None = None) -> None:
        self._state = state or BlockedSenderState()

    async def read_state(self) -> BlockedSenderState:
        return self._state


class StaticPlanner:
    async def create_plan(self, _request_text: str):
        return direct_add_plan((domain("new.example"),), "Mail rejected.")


class FailingPlanner:
    async def create_plan(self, _request_text: str):
        message = "controlled planner failure"
        raise RuntimeError(message)


class SuccessfulLiveRunner:
    def __init__(self) -> None:
        self.confirmations = []

    async def execute(self, _run, confirmation) -> RunResult:
        self.confirmations.append(confirmation)
        return RunResult(
            status=RunStatus.APPLIED_PENDING_PROPAGATION,
            propagation_pending=True,
        )


class MemoryOwnershipStore:
    def __init__(self, registry: OwnershipRegistry | None = None) -> None:
        self.registry = registry or OwnershipRegistry()

    def load(self) -> OwnershipRegistry:
        return self.registry

    def save(self, registry: OwnershipRegistry) -> None:
        self.registry = registry


def _settings(tmp_path: Path, mode: RunMode = RunMode.PLAN_ONLY) -> Settings:
    return Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        run_mode=mode,
        expected_admin_email="admin@example.com",
        expected_workspace_domain="example.com",
    )


def _pack(status: str = "read_live_validated") -> UiContractPack:
    values = {
        "contract_id": SECOND_ID,
        "created_at": NOW,
        "status": status,
        "fixture_hashes": ("1" * 64,),
        "contract_names": ("blocked_senders_root",),
    }
    if status != "accepted":
        return UiContractPack(**values)
    unsigned = UiContractPack.model_construct(**values, accepted_digest=None)
    return UiContractPack(**values, accepted_digest=contract_pack_digest(unsigned))


def _metadata() -> RunManifestMetadata:
    return RunManifestMetadata(
        application_version="0.1.0",
        git_commit=None,
        dirty_working_tree=None,
        python_version="3.13",
        agent_framework_version="1.11.0",
        playwright_version="1.61.0",
        browser_version=None,
        pydantic_version="2.13.4",
        ollama_version=None,
        model_tag=None,
        model_digest=None,
        operating_system="test",
        start_time=NOW,
    )


def _connect(client: TestClient, launch_token: str) -> str:
    response = client.post(
        "/bootstrap",
        data={"token": launch_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, RunMode.PLAN_ONLY),
        ({"plan_only": False}, RunMode.DRY_RUN),
        ({"plan_only": False, "dry_run": False}, RunMode.LIVE),
        ({"run_mode": RunMode.DRY_RUN}, RunMode.DRY_RUN),
    ],
)
def test_run_mode_migration_is_unambiguous(tmp_path: Path, values: dict, expected: RunMode) -> None:
    settings = Settings(
        profile_dir=tmp_path / "profile",
        audit_dir=tmp_path / "audit",
        state_dir=tmp_path / "state",
        expected_admin_email="admin@example.com",
        expected_workspace_domain="example.com",
        **values,
    )

    assert settings.run_mode == expected
    assert settings.plan_only is (expected == RunMode.PLAN_ONLY)
    assert settings.dry_run is (expected != RunMode.LIVE)


def test_run_mode_rejects_mixed_new_and_legacy_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        Settings(
            profile_dir=tmp_path / "profile",
            audit_dir=tmp_path / "audit",
            state_dir=tmp_path / "state",
            run_mode=RunMode.DRY_RUN,
            dry_run=True,
        )


@pytest.mark.asyncio
async def test_dry_run_calculates_and_finalizes_without_a_writer_dependency(tmp_path: Path) -> None:
    plan = direct_add_plan((domain("new.example"),), "Mail rejected.")
    writer = RunAuditWriter(tmp_path / "audit" / "runs" / f"20260711T150000Z-{'0' * 32}")
    auditor = WorkflowAuditService(writer, FixedClock(), "0" * 32)
    service = DryRunService(
        DryRunDependencies(
            preflight=ReadyPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=auditor,
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )

    result = await service.preview(plan)
    await DryRunAuditFinalizationService(writer, FixedClock(), "0" * 32, _metadata()).finalize(
        result
    )
    catalog = AuditCatalog(tmp_path / "audit")

    assert result.status == "preview_ready"
    assert result.change_set is not None
    assert result.change_set.has_mutations
    assert result.impact is not None
    assert result.impact.level == "standard"
    assert (writer.run_directory / "dry-run.json").exists()
    assert catalog.list_runs()[0].integrity_valid


@pytest.mark.asyncio
async def test_dry_run_blocks_failed_preflight_with_no_state_evidence(tmp_path: Path) -> None:
    writer = RunAuditWriter(tmp_path / "run")
    service = DryRunService(
        DryRunDependencies(
            preflight=FailedPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=WorkflowAuditService(writer, FixedClock(), "run"),
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )

    result = await service.preview(direct_list_plan())
    await DryRunAuditFinalizationService(writer, FixedClock(), "run", _metadata()).finalize(result)

    assert result.status == "blocked"
    assert result.reason_code == "dry_run_preflight_not_ready"
    assert not (writer.run_directory / "before.json").exists()


def test_impact_assessment_marks_broad_and_destructive_changes() -> None:
    before = owned_state(entries=(domain("a.example"), domain("b.example")))
    changed = owned_state(entries=())
    change_set = calculate_change_set(before, changed)
    impact = assess_impact(
        change_set,
        DesiredStateResult(desired_state=changed, notice_affected_entry_count=3),
        ownership_verified=True,
    )
    destructive_set = calculate_change_set(before, BlockedSenderState())
    destructive = assess_impact(
        destructive_set,
        DesiredStateResult(desired_state=BlockedSenderState()),
        ownership_verified=True,
    )

    assert impact.level == "broad"
    assert impact.affected_entries == 3
    assert destructive.level == "destructive"


def test_approval_uses_server_hashes_expires_and_is_single_use() -> None:
    plan = direct_list_plan()
    state = BlockedSenderState()
    change_set = calculate_change_set(state, state)
    preview = DryRunResult(
        status="preview_ready",
        plan=plan,
        current_state=state,
        desired_state=state,
        change_set=change_set.model_copy(update={"rules_to_create": owned_state().rules}),
        impact=assess_impact(
            change_set.model_copy(update={"rules_to_create": owned_state().rules}),
            DesiredStateResult(desired_state=owned_state()),
            ownership_verified=True,
        ),
        plan_hash=canonical_hash(plan),
        before_state_hash=canonical_hash(state),
        change_set_hash=canonical_hash(change_set),
    )
    service = ApprovalService(600)
    pending = service.issue("0" * 32, preview, NOW)

    with pytest.raises(ValueError, match="phrase"):
        service.approve(
            "0" * 32,
            phrase="wrong",
            acknowledged=True,
            approval_id="approval-1",
            now=NOW,
        )
    response = service.approve(
        "0" * 32,
        phrase=pending.phrase,
        acknowledged=True,
        approval_id="approval-1",
        now=NOW,
    )

    assert response.plan_hash == preview.plan_hash
    assert service.get("0" * 32, NOW) is None


def test_approval_expiration_and_cancellation_are_closed() -> None:
    service = ApprovalService(60)
    blocked_preview = DryRunResult(
        status="blocked",
        plan=direct_list_plan(),
        plan_hash="0" * 64,
        reason_code="blocked",
    )
    with pytest.raises(ValueError, match="complete mutation preview"):
        service.issue("0" * 32, blocked_preview, NOW)
    assert service.get("0" * 32, NOW) is None
    service.cancel("0" * 32)
    with pytest.raises(ValueError, match="missing or expired"):
        service.approve(
            "0" * 32,
            phrase="APPLY 0000",
            acknowledged=True,
            approval_id="expired",
            now=NOW + timedelta(minutes=2),
        )


def test_ui_contract_store_requires_exact_accepted_digest(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    path = state / "ui-contract-pack.json"
    accepted = _pack("accepted")
    empty = UiContractStore(state)
    assert empty.load() is None
    with pytest.raises(UnvalidatedUiContract, match="accepted UI contract"):
        empty.require_accepted()
    path.write_text(accepted.model_dump_json(), encoding="utf-8")
    store = UiContractStore(state)

    assert store.require_accepted() == accepted
    tampered = accepted.model_copy(update={"contract_names": ("tampered",)})
    path.write_text(tampered.model_dump_json(), encoding="utf-8")
    with pytest.raises(UnvalidatedUiContract, match="digest"):
        store.load()


def test_ownership_health_never_adopts_managed_looking_resources() -> None:
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("a.example"),))
    healthy = assess_ownership_health(
        state,
        registry_for(SECOND_ID),
        "[Compliance Agent]",
    )
    missing = assess_ownership_health(state, OwnershipRegistry(), "[Compliance Agent]")

    assert healthy[0].status == "healthy"
    assert missing[0].status == "registry_missing"
    assert not missing[0].recoverable_from_audit


def test_propagation_records_are_atomic_and_mail_flow_requires_evidence(tmp_path: Path) -> None:
    service = PropagationService(tmp_path / "state")
    assert service.list() == ()
    pending = service.create_pending("0" * 32, NOW)
    rechecked = service.record_ui_recheck("0" * 32, "1" * 32, NOW)
    verified = service.record_mail_flow("0" * 32, "2" * 32, NOW)

    assert pending.status == "pending"
    assert rechecked.status == "ui_reconfirmed"
    assert verified.status == "mail_flow_verified"
    assert service.list() == (verified,)
    with pytest.raises(ValueError, match="does not exist"):
        service.record_ui_recheck("f" * 32, "1" * 32, NOW)


def test_protected_json_rejects_non_collection_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "values.json"
    path.write_text("{}", encoding="utf-8")
    store = ProtectedJsonStore(path)
    with pytest.raises(TypeError, match="array"):
        store.load(PropagationRecord)


@pytest.mark.asyncio
async def test_composed_dry_run_runtime_records_contract_digest(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RunMode.DRY_RUN)
    pack = _pack()
    runtime = compose_dry_run_runtime(
        settings,
        AcceptedReadAdapters(
            preflight=ReadyPreflight(),
            current_reader=StaticReader(),
            contract_pack=pack,
        ),
        clock=FixedClock(),
        identifiers=FixedIdentifiers(UUID(int=0), SECOND_ID),
        repository=tmp_path,
    )

    result = await runtime.preview("List blocked senders", direct_list_plan())
    manifest = json.loads((runtime.run_directory / "manifest.json").read_text(encoding="utf-8"))
    runtime.close()

    assert result.status == "no_change"
    assert manifest["ui_contract_digest"] == contract_pack_digest(pack)


@pytest.mark.asyncio
async def test_console_coordinator_advances_preview_and_closes_planner_failure(
    tmp_path: Path,
) -> None:
    approval_service = ApprovalService(600)
    writer = RunAuditWriter(tmp_path / "preview")
    dry_run = DryRunService(
        DryRunDependencies(
            preflight=ReadyPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=WorkflowAuditService(writer, FixedClock(), "run"),
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )
    coordinator = ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=StaticPlanner(),
            identifiers=FixedIdentifiers(UUID(int=0)),
            clock=FixedClock().now,
            approval_service=approval_service,
            dry_run_service=dry_run,
        )
    )

    created = await coordinator.create("Block new.example", RunMode.LIVE)
    previewed = await coordinator.preview(created.run_id)

    assert previewed.phase.value == "awaiting_approval"
    assert coordinator.pending_approval(created.run_id) is not None
    assert coordinator.list_runs() == (previewed,)
    assert coordinator.cancel(created.run_id).phase.value == "cancelled"

    failed = ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=FailingPlanner(),
            identifiers=FixedIdentifiers(UUID(int=1)),
            clock=FixedClock().now,
            approval_service=ApprovalService(600),
        )
    )
    blocked = await failed.create("Fail planning", RunMode.PLAN_ONLY)
    assert blocked.phase.value == "blocked"
    assert blocked.error_code == "RuntimeError"
    plan_only = await coordinator.create("Plan only", RunMode.PLAN_ONLY)
    with pytest.raises(ValueError, match="not eligible"):
        await coordinator.preview(plan_only.run_id)


@pytest.mark.asyncio
async def test_console_coordinator_approval_executes_only_injected_runner(tmp_path: Path) -> None:
    runner = SuccessfulLiveRunner()
    approvals = ApprovalService(600)
    dry_run = DryRunService(
        DryRunDependencies(
            preflight=ReadyPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=WorkflowAuditService(
                RunAuditWriter(tmp_path / "preview"),
                FixedClock(),
                "run",
            ),
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )
    coordinator = ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=StaticPlanner(),
            identifiers=FixedIdentifiers(UUID(int=3)),
            clock=FixedClock().now,
            approval_service=approvals,
            dry_run_service=dry_run,
            live_runner=runner,
        )
    )
    run = await coordinator.create("Block new.example", RunMode.LIVE)
    await coordinator.preview(run.run_id)
    pending = coordinator.pending_approval(run.run_id)
    assert pending is not None

    completed = await coordinator.approve(
        run.run_id,
        phrase=pending.phrase,
        acknowledged=True,
        approval_id="approval-1",
    )

    assert completed.phase.value == "completed"
    assert completed.result is not None
    assert completed.result.propagation_pending
    assert len(runner.confirmations) == 1
    with pytest.raises(ValueError, match="cannot be cancelled"):
        coordinator.cancel(run.run_id)


def test_console_security_and_primary_operator_flow(tmp_path: Path) -> None:  # noqa: PLR0915
    settings = _settings(tmp_path)
    console = create_console_app(settings, planner=StaticPlanner())
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")

    redirect = client.get("/", follow_redirects=False)
    bad_host = client.get("/bootstrap", headers={"host": "evil.example"})
    invalid = client.post("/bootstrap", data={"token": "wrong"})
    launch_token = console.security.launch_token
    connected = client.post(
        "/bootstrap",
        data={"token": launch_token},
        headers={"origin": "null"},
        follow_redirects=False,
    )

    assert redirect.status_code == 303
    assert bad_host.status_code == 400
    assert invalid.status_code == 403
    assert connected.status_code == 303
    assert "httponly" in connected.headers["set-cookie"].lower()
    assert "samesite=strict" in connected.headers["set-cookie"].lower()

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Plan a change" in dashboard.text
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]

    csrf = console.security.csrf_token()
    forbidden = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "plan_only", "csrf_token": "bad"},
    )
    bad_origin = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "plan_only", "csrf_token": csrf},
        headers={"origin": "https://evil.example"},
    )
    created = client.post(
        "/runs",
        data={"request_text": "Block new.example", "mode": "dry_run", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert forbidden.status_code == 403
    assert bad_origin.status_code == 403
    assert created.status_code == 303
    run_url = created.headers["location"]
    detail = client.get(run_url)
    assert "Validated actions" in detail.text
    assert "add_blocked_entries" in detail.text
    direct = client.post(
        "/runs/direct-add",
        data={
            "target": "direct.example",
            "target_kind": "domain",
            "notice": "Mail rejected.",
            "mode": "plan_only",
            "csrf_token": csrf,
        },
    )
    assert "direct.example" in direct.text
    events = client.get(f"{run_url}/events")
    assert "event: phase" in events.text
    assert "event: settled" in events.text
    assert "workflow-track" in events.text

    preview = client.post(f"{run_url}/preview", data={"csrf_token": csrf})
    assert "ui contract pack required" in preview.text.lower()
    cancelled = client.post(f"{run_url}/cancel", data={"csrf_token": csrf})
    assert "Cancelled" in cancelled.text

    for path, text in (
        ("/readiness", "Readiness"),
        ("/runs/new", "Run mode"),
        ("/contracts", "No contract pack installed"),
        ("/ownership", "No local ownership records"),
        ("/audits", "No finalized audit runs"),
        ("/propagation", "No applied runs"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert text in response.text

    missing_cancel = client.post(
        f"/runs/{'f' * 32}/cancel",
        data={"csrf_token": csrf},
    )
    assert missing_cancel.status_code == 400


@pytest.mark.asyncio
async def test_console_renders_real_audit_contract_ownership_and_propagation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    state_directory = settings.state_dir
    state_directory.mkdir(mode=0o700, parents=True)
    accepted = _pack("accepted")
    (state_directory / "ui-contract-pack.json").write_text(
        accepted.model_dump_json(),
        encoding="utf-8",
    )
    OwnershipStore(state_directory).save(registry_for(SECOND_ID))
    PropagationService(state_directory).create_pending("a" * 32, NOW)

    run_id = "b" * 32
    writer = RunAuditWriter(settings.audit_dir / "runs" / f"20260711T150000Z-{run_id}")
    auditor = WorkflowAuditService(writer, FixedClock(), run_id)
    service = DryRunService(
        DryRunDependencies(
            preflight=ReadyPreflight(),
            reader=StaticReader(),
            change_service=ChangeService(FixedIdentifiers(), "[Compliance Agent]"),
            ownership_store=MemoryOwnershipStore(),
            auditor=auditor,
            expected_admin_email="admin@example.com",
            expected_workspace_domain="example.com",
        )
    )
    result = await service.preview(direct_list_plan())
    await DryRunAuditFinalizationService(writer, FixedClock(), run_id, _metadata()).finalize(result)

    console = create_console_app(settings, planner=StaticPlanner())
    client = TestClient(console.app, base_url="http://127.0.0.1:8765")
    _connect(client, console.security.launch_token)

    assert "Accepted" in client.get("/contracts").text
    assert "Block rule" in client.get("/ownership").text
    assert "Pending" in client.get("/propagation").text
    audits = client.get("/audits")
    assert run_id[:12] in audits.text
    detail = client.get(f"/audits/{run_id}")
    assert detail.status_code == 200
    assert "Integrity verified" in detail.text
    assert "dry_run_preview_ready" not in detail.text

    expired = settings.audit_dir / "runs" / f"20000101T000000Z-{'0' * 32}"
    expired.mkdir()
    csrf = console.security.csrf_token()
    wrong = client.post(
        "/audits/prune",
        data={"csrf_token": csrf, "confirmation": "wrong"},
    )
    applied = client.post(
        "/audits/prune",
        data={"csrf_token": csrf, "confirmation": "DELETE 1 RUNS"},
    )
    assert wrong.status_code == 400
    assert applied.status_code == 200
    assert not expired.exists()


def test_console_bootstrap_url_keeps_token_in_fragment(tmp_path: Path) -> None:
    console = create_console_app(_settings(tmp_path), planner=StaticPlanner())

    assert "/bootstrap#" in console.security.bootstrap_url
    assert "?" not in console.security.bootstrap_url
    assert mask_identity("") == "Not configured"
    assert mask_identity("example.com") == "ex••••"


def test_fixture_inspection_hashes_safe_evidence_and_rejects_auth_capture(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "metadata.json").write_text(
        json.dumps({"detected_state": "blocked_senders"}),
        encoding="utf-8",
    )
    (fixture / "page.html").write_text("<main>Blocked senders</main>", encoding="utf-8")
    (fixture / "aria.txt").write_text("heading Blocked senders", encoding="utf-8")

    valid = inspect_fixture_directory(fixture)
    (fixture / "metadata.json").write_text(
        json.dumps({"detected_state": "login_required"}),
        encoding="utf-8",
    )
    prohibited = inspect_fixture_directory(fixture)

    assert valid.valid
    assert set(valid.file_hashes) == {"metadata.json", "page.html", "aria.txt"}
    assert not prohibited.valid
    assert "authentication_page_capture_prohibited" in prohibited.errors


def test_fixture_inspection_closes_missing_binary_and_sensitive_evidence(tmp_path: Path) -> None:
    missing = inspect_fixture_directory(tmp_path / "missing")
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "metadata.json").write_text("[]", encoding="utf-8")
    (fixture / "page.html").write_bytes(b"\xff\xfe")
    (fixture / "aria.txt").write_text("password=secret", encoding="utf-8")
    unsafe = inspect_fixture_directory(fixture)

    assert missing.errors == ("fixture_not_regular",)
    assert not unsafe.valid
    assert "metadata_not_object" in unsafe.errors
    assert "invalid_utf8_page.html" in unsafe.errors
    assert "prohibited_sensitive_pattern_aria.txt" in unsafe.errors
    (fixture / "metadata.json").write_text("{bad", encoding="utf-8")
    malformed = inspect_fixture_directory(fixture)
    assert "invalid_metadata" in malformed.errors


@pytest.mark.asyncio
async def test_ownership_recovery_requires_intact_audit_and_exact_confirmation(
    tmp_path: Path,
) -> None:
    state = owned_state(ownership_id=SECOND_ID, entries=(domain("safe.example"),))
    run = tmp_path / "run"
    writer = RunAuditWriter(run)
    auditor = WorkflowAuditService(writer, FixedClock(), "recovery-run")
    auditor.record_state("after", state)
    await AuditFinalizationService(
        writer,
        FixedClock(),
        "recovery-run",
        _metadata(),
    ).finalize(RunResult(status=RunStatus.NO_CHANGE_REQUIRED))
    store = MemoryOwnershipStore()
    service = OwnershipRecoveryService(store)

    with pytest.raises(OwnershipNotEstablished, match="exact confirmation"):
        service.recover(SECOND_ID, state, run, "wrong")
    record = service.recover(
        SECOND_ID,
        state,
        run,
        f"RECOVER {SECOND_ID.hex[:8].upper()}",
    )

    assert record.ownership_id == SECOND_ID
    assert store.registry.find(SECOND_ID) == record
    with pytest.raises(OwnershipNotEstablished, match="already contains"):
        service.recover(
            SECOND_ID,
            state,
            run,
            f"RECOVER {SECOND_ID.hex[:8].upper()}",
        )

    mismatched = owned_state(ownership_id=SECOND_ID, entries=(domain("changed.example"),))
    fresh_store = MemoryOwnershipStore()
    with pytest.raises(OwnershipNotEstablished, match="does not match"):
        OwnershipRecoveryService(fresh_store).recover(
            SECOND_ID,
            mismatched,
            run,
            f"RECOVER {SECOND_ID.hex[:8].upper()}",
        )


def test_redacted_zip_export_is_deterministic_and_manifested(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "request.txt").write_text("Contact admin@example.com", encoding="utf-8")
    destination = tmp_path / "redacted.zip"

    exported = export_redacted_zip(source, destination)
    with zipfile.ZipFile(exported) as archive:
        names = archive.namelist()
        request = archive.read("request.txt").decode("utf-8")
        manifest = json.loads(archive.read("export-manifest.json"))

    assert names == sorted(names)
    assert "admin@example.com" not in request
    assert "a***@example.com" in request
    assert manifest["artifacts"][0]["path"] == "request.txt"
    with pytest.raises(AuditWriteFailure, match="already exists"):
        export_redacted_zip(source, destination)
