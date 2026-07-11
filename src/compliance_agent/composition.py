"""Production composition root for accepted, externally supplied Admin-console adapters."""

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from agent_framework import Workflow

from compliance_agent.application.audit_service import AuditFinalizationService
from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.failure_mapping import (
    FailureMappingPreflight,
    FailureMappingReader,
    FailureMappingWriter,
    MutationSource,
    PreflightSource,
    StateSource,
)
from compliance_agent.application.ownership_service import OwnershipLifecycleService
from compliance_agent.application.workflow_audit_service import WorkflowAuditService
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.infrastructure.clock import Clock, SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.infrastructure.identifiers import IdentifierGenerator, Uuid4Generator
from compliance_agent.infrastructure.process_lock import ProcessLock
from compliance_agent.infrastructure.runtime_metadata import collect_manifest_metadata
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


def compose_compliance_runtime(
    settings: Settings,
    adapters: AcceptedAdapters,
    *,
    clock: Clock | None = None,
    identifiers: IdentifierGenerator | None = None,
    repository: Path | None = None,
) -> ComplianceRuntime:
    """Bind accepted adapters to protected persistence and the fixed workflow graph."""

    if settings.plan_only or settings.dry_run:
        message = (
            "the mutation-capable compliance runtime requires CA_PLAN_ONLY=false "
            "and CA_DRY_RUN=false"
        )
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
        metadata = collect_manifest_metadata(start_time, settings, repository)
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
