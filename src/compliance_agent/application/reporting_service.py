"""Deterministic authoritative report builder."""

from compliance_agent.audit.report import render_report_json, render_report_markdown
from compliance_agent.schemas.results import RunResult


class ReportingService:
    """Render already-decided run facts; never reinterpret status."""

    def build(self, result: RunResult) -> tuple[str, str]:
        """Return authoritative JSON and deterministic Markdown."""

        return render_report_json(result), render_report_markdown(result)
