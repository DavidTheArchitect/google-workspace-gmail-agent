"""Read-only inspection projections over one finalized audit run directory."""

import json
import re
from pathlib import Path

from pydantic import BaseModel

from compliance_agent.audit.manifest import RunManifest
from compliance_agent.audit.writer import verify_event_chain
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.events import AuditEvent
from compliance_agent.schemas.operations import DryRunResult
from compliance_agent.schemas.results import RunResult

_LINE_ERROR = re.compile(r"^line (\d+): (.*)$", re.DOTALL)


class AuditEventView(FrozenModel):
    """One event-stream line with its parsed event and attributed chain errors."""

    line_number: int
    event: AuditEvent | None = None
    errors: tuple[str, ...] = ()


class AuditRunInspection(FrozenModel):
    """Everything the console can honestly show about one immutable run."""

    manifest: RunManifest | None = None
    events: tuple[AuditEventView, ...] = ()
    stream_errors: tuple[str, ...] = ()
    report: RunResult | None = None
    dry_run: DryRunResult | None = None
    raw_report: str | None = None


def inspect_audit_run(run_directory: Path) -> AuditRunInspection:
    """Project one run directory into display-ready evidence; never mutate it."""

    events, stream_errors = _load_events(run_directory / "run.jsonl")
    return AuditRunInspection(
        manifest=_load_model(run_directory / "manifest.json", RunManifest),
        events=events,
        stream_errors=stream_errors,
        report=_load_model(run_directory / "report.json", RunResult),
        dry_run=_load_model(run_directory / "dry-run.json", DryRunResult),
        raw_report=(
            _pretty_json(run_directory / "report.json")
            or _pretty_json(run_directory / "dry-run.json")
        ),
    )


def _load_model[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT | None:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def _load_events(path: Path) -> tuple[tuple[AuditEventView, ...], tuple[str, ...]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return (), ("event stream is unavailable",)
    line_errors: dict[int, list[str]] = {}
    stream_errors: list[str] = []
    for error in verify_event_chain(path):
        match = _LINE_ERROR.match(error)
        if match:
            line_errors.setdefault(int(match.group(1)), []).append(match.group(2))
        else:
            stream_errors.append(error)
    views: list[AuditEventView] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            event: AuditEvent | None = AuditEvent.model_validate_json(line)
        except ValueError:
            event = None
        views.append(
            AuditEventView(
                line_number=line_number,
                event=event,
                errors=tuple(line_errors.get(line_number, ())),
            )
        )
    return tuple(views), tuple(stream_errors)


def _pretty_json(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
