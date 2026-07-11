"""Deterministic JSON and Markdown report rendering."""

import json

from compliance_agent.schemas.results import RunResult


def render_report_json(result: RunResult) -> str:
    """Render the authoritative report with stable ordering."""

    return (
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def render_report_markdown(result: RunResult) -> str:
    """Render deterministic human-readable text from authoritative fields only."""

    lines = ["# Compliance agent run report", "", f"Status: `{result.status.value}`"]
    if result.error_code:
        lines.extend(("", f"Error code: `{result.error_code}`"))
    if result.requested_changes:
        lines.extend(("", "## Requested changes", ""))
        lines.extend(f"- {change}" for change in result.requested_changes)
    if result.verified_changes:
        lines.extend(("", "## Verified changes", ""))
        lines.extend(f"- {change}" for change in result.verified_changes)
    if result.warnings:
        lines.extend(("", "## Warnings", ""))
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines) + "\n"
