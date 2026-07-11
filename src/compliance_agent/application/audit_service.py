"""Concrete deterministic terminal audit finalization."""

from compliance_agent.audit.report import render_report_json, render_report_markdown
from compliance_agent.audit.writer import RunAuditWriter
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.results import RunResult


class AuditFinalizationService:
    """Persist authoritative terminal reports and the final hash-chained event."""

    def __init__(self, writer: RunAuditWriter, clock: Clock, run_id: str) -> None:
        self._writer = writer
        self._clock = clock
        self._run_id = run_id

    async def finalize(self, result: RunResult) -> None:
        """Write report artifacts and terminal event without changing status facts."""

        self._writer.write_text("report.json", render_report_json(result))
        self._writer.write_text("report.md", render_report_markdown(result))
        self._writer.append(
            AuditEvent(
                run_id=self._run_id,
                sequence=self._writer.next_sequence,
                timestamp=self._clock.now(),
                event_type="run_finalized",
                component="audit_finalization_service",
                outcome=result.status.value,
                error_code=result.error_code,
            )
        )
