"""Concrete deterministic terminal audit finalization."""

from compliance_agent.audit.manifest import (
    RunManifest,
    RunManifestMetadata,
    digest_artifacts,
)
from compliance_agent.audit.report import render_report_json, render_report_markdown
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.results import RunResult


class AuditFinalizationService:
    """Persist authoritative terminal reports and the final hash-chained event."""

    def __init__(
        self,
        writer: RunAuditWriter,
        clock: Clock,
        run_id: str,
        manifest_metadata: RunManifestMetadata,
    ) -> None:
        self._writer = writer
        self._clock = clock
        self._run_id = run_id
        self._manifest_metadata = manifest_metadata

    async def finalize(self, result: RunResult) -> None:
        """Write report artifacts and terminal event without changing status facts."""

        self._writer.write_text("report.json", render_report_json(result))
        self._writer.write_text("report.md", render_report_markdown(result))
        end_time = self._clock.now()
        self._writer.append(
            AuditEvent(
                run_id=self._run_id,
                sequence=self._writer.next_sequence,
                timestamp=end_time,
                event_type="run_finalized",
                component="audit_finalization_service",
                outcome=result.status.value,
                error_code=result.error_code,
            )
        )
        manifest = RunManifest(
            **self._manifest_metadata.model_dump(),
            end_time=end_time,
            final_status=result.status,
            artifacts=digest_artifacts(self._writer.run_directory),
        )
        self._writer.write_text("manifest.json", manifest.model_dump_json(indent=2) + "\n")
