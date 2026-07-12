"""Terminal protected audit finalization for read-only previews."""

from compliance_agent.audit.manifest import RunManifest, RunManifestMetadata, digest_artifacts
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.operations import DryRunResult
from compliance_agent.schemas.status import RunStatus


class DryRunAuditFinalizationService:
    """Persist authoritative dry-run evidence without a mutation result."""

    def __init__(
        self,
        writer: RunAuditWriter,
        clock: Clock,
        run_id: str,
        metadata: RunManifestMetadata,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._run_id = run_id
        self._metadata = metadata

    async def finalize(self, result: DryRunResult) -> None:
        self._writer.write_text("dry-run.json", result.model_dump_json(indent=2) + "\n")
        end_time = self._clock.now()
        status = _dry_run_status(result)
        self._writer.append(
            AuditEvent(
                run_id=self._run_id,
                sequence=self._writer.next_sequence,
                timestamp=end_time,
                event_type="dry_run_finalized",
                component="dry_run_audit_finalization_service",
                outcome=result.status,
                error_code=result.reason_code,
            )
        )
        manifest = RunManifest(
            **self._metadata.model_dump(),
            end_time=end_time,
            final_status=status,
            artifacts=digest_artifacts(self._writer.run_directory),
        )
        self._writer.write_text("manifest.json", manifest.model_dump_json(indent=2) + "\n")


def _dry_run_status(result: DryRunResult) -> RunStatus:
    if result.status == "preview_ready":
        return RunStatus.DRY_RUN_PREVIEW_READY
    if result.status == "no_change":
        return RunStatus.NO_CHANGE_REQUIRED
    return RunStatus.FAILED_UNCHANGED
