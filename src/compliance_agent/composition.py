"""Production composition root for accepted, externally supplied Admin-console adapters."""

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from agent_framework import Workflow

from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.dry_run_audit_service import DryRunAuditFinalizationService
from compliance_agent.application.dry_run_service import DryRunDependencies, DryRunService
from compliance_agent.application.failure_mapping import (
    FailureMappingPreflight,
    FailureMappingReader,
    FailureMappingWriter,
    MutationSource,
    PreflightSource,
    StateSource,
)
from compliance_agent.application.ownership_service import OwnershipLifecycleService
from compliance_agent.application.ui_contract_service import contract_pack_digest
from compliance_agent.application.workflow_audit_service import WorkflowAuditService
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.infrastructure.clock import Clock, SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.identifiers import IdentifierGenerator, Uuid4Generator
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.infrastructure.runtime_metadata import collect_manifest_metadata
from compliance_agent.schemas.operations import DryRunResult, RunMode, UiContractPack
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.settings import Settings
from compliance_agent.version import __version__
from compliance_agent.workflow.build import WorkflowDependencies, build_compliance_workflow
from compliance_agent.workflow.contracts import PlannerAdapter


@dataclass(frozen=True, slots=True)
class AcceptedAdapters:
    """UI adapters admitted only after the supervised selector acceptance gate."""

    planner: PlannerAdapter
    preflight: PreflightSource
    current_reader: StateSource
    verification_reader: StateSource
    writer: MutationSource
    contract_pack: UiContractPack


@dataclass(frozen=True, slots=True)
class AcceptedReadAdapters:
    """Read-only UI adapters backed by at least supervised live-read evidence."""

    preflight: PreflightSource
    current_reader: StateSource
    contract_pack: UiContractPack


@dataclass(frozen=True, slots=True)
class ComplianceRuntime:
    """Composed workflow with an already-acquired exclusive run lock."""

    workflow: Workflow
    run_id: str
    run_directory: Path
    run_lock: ProcessLock

    def close(self) -> None:
        """Release the run lock after workflow and browser resources have stopped."""

        self.run_lock.release()

    def __enter__(self) -> "ComplianceRuntime":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class DryRunRuntime:
    """Read-only preview service with protected audit finalization and an exclusive lock."""

    service: DryRunService
    finalizer: DryRunAuditFinalizationService
    auditor: WorkflowAuditService
    run_id: str
    run_directory: Path
    run_lock: ProcessLock

    async def preview(self, request_text: str, plan: TaskPlan) -> DryRunResult:
        self.auditor.record_request(request_text)
        result = await self.service.preview(plan)
        await self.finalizer.finalize(result)
        return result

    def close(self) -> None:
        self.run_lock.release()

    def __enter__(self) -> "DryRunRuntime":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def compose_compliance_runtime(
    settings: Settings,
    adapters: AcceptedAdapters,
    *,
    clock: Clock | None = None,
    identifiers: IdentifierGenerator | None = None,
    repository: Path | None = None,
) -> ComplianceRuntime:
    """Bind accepted adapters to protected persistence and the fixed workflow graph."""

    if settings.run_mode != RunMode.LIVE:
        message = (
            "the mutation-capable compliance runtime requires CA_PLAN_ONLY=false "
            "and CA_DRY_RUN=false"
        )
        raise ValueError(message)
    if (
        adapters.contract_pack.status != "accepted"
        or adapters.contract_pack.accepted_digest is None
    ):
        message = "live runtime requires an accepted UI contract pack"
        raise ValueError(message)
    actual_clock = clock or SystemClock()
    actual_identifiers = identifiers or Uuid4Generator()
    start_time = actual_clock.now()
    run_id = actual_identifiers.new().hex
    timestamp = start_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_directory = settings.audit_dir / "runs" / f"{timestamp}-{run_id}"
    run_lock = ProcessLock(
        settings.state_dir / "run.lock",
        run_id=run_id,
        started_at=start_time,
        application_version=__version__,
    )
    run_lock.acquire()
    try:
        writer = RunAuditWriter(run_directory)
        auditor = WorkflowAuditService(writer, actual_clock, run_id)
        metadata = collect_manifest_metadata(
            start_time,
            settings,
            repository,
            adapters.contract_pack.accepted_digest,
        )
        finalizer = AuditFinalizationService(writer, actual_clock, run_id, metadata)
        ownership_store = OwnershipStore(settings.state_dir)
        dependencies = WorkflowDependencies(
            planner=adapters.planner,
            preflight=FailureMappingPreflight(adapters.preflight),
            current_reader=FailureMappingReader(adapters.current_reader),
            verification_reader=FailureMappingReader(adapters.verification_reader),
            writer=FailureMappingWriter(adapters.writer),
            audit_finalizer=finalizer,
            auditor=auditor,
            change_service=ChangeService(actual_identifiers, settings.managed_resource_prefix),
            ownership_registry=ownership_store.load(),
            expected_admin_email=settings.expected_admin_email,
            expected_workspace_domain=settings.expected_workspace_domain,
            audit_directory=str(run_directory),
            ownership_lifecycle=OwnershipLifecycleService(ownership_store, actual_clock),
        )
        return ComplianceRuntime(
            workflow=build_compliance_workflow(dependencies),
            run_id=run_id,
            run_directory=run_directory,
            run_lock=run_lock,
        )
    except BaseException:
        run_lock.release()
        raise


def compose_dry_run_runtime(
    settings: Settings,
    adapters: AcceptedReadAdapters,
    *,
    clock: Clock | None = None,
    identifiers: IdentifierGenerator | None = None,
    repository: Path | None = None,
) -> DryRunRuntime:
    """Bind supervised read adapters to a writer-free preview service and audit package."""

    # LIVE-mode previews use this same writer-free composition before a separate
    # approval boundary admits the mutation-capable runtime.
    if settings.run_mode not in {RunMode.DRY_RUN, RunMode.LIVE}:
        message = "dry-run runtime requires CA_RUN_MODE=dry_run or live"
        raise ValueError(message)
    accepted_read_statuses = {"read_live_validated", "write_live_validated", "accepted"}
    if adapters.contract_pack.status not in accepted_read_statuses:
        message = "dry-run runtime requires supervised live-read contract evidence"
        raise ValueError(message)
    actual_clock = clock or SystemClock()
    actual_identifiers = identifiers or Uuid4Generator()
    start_time = actual_clock.now()
    run_id = actual_identifiers.new().hex
    timestamp = start_time.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_directory = settings.audit_dir / "runs" / f"{timestamp}-{run_id}"
    run_lock = ProcessLock(
        settings.state_dir / "run.lock",
        run_id=run_id,
        started_at=start_time,
        application_version=__version__,
    )
    run_lock.acquire()
    try:
        writer = RunAuditWriter(run_directory)
        auditor = WorkflowAuditService(writer, actual_clock, run_id)
        metadata = collect_manifest_metadata(
            start_time,
            settings,
            repository,
            contract_pack_digest(adapters.contract_pack),
        )
        ownership_store = OwnershipStore(settings.state_dir)
        service = DryRunService(
            DryRunDependencies(
                preflight=FailureMappingPreflight(adapters.preflight),
                reader=FailureMappingReader(adapters.current_reader),
                change_service=ChangeService(
                    actual_identifiers,
                    settings.managed_resource_prefix,
                ),
                ownership_store=ownership_store,
                auditor=auditor,
                expected_admin_email=settings.expected_admin_email,
                expected_workspace_domain=settings.expected_workspace_domain,
            )
        )
        return DryRunRuntime(
            service=service,
            finalizer=DryRunAuditFinalizationService(
                writer,
                actual_clock,
                run_id,
                metadata,
            ),
            auditor=auditor,
            run_id=run_id,
            run_directory=run_directory,
            run_lock=run_lock,
        )
    except BaseException:
        run_lock.release()
        raise
