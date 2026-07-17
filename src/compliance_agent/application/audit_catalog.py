"""Read-only audit history projections for the local operator console."""

import re
from datetime import UTC, datetime
from pathlib import Path

from compliance_agent.audit.manifest import RunManifest, verify_manifest
from compliance_agent.audit.writer import verify_event_chain
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.status import RunStatus

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_RUN_TIMESTAMP = "%Y%m%dT%H%M%SZ"


class AuditRunSummary(FrozenModel):
    """One safe history row derived from a terminal manifest."""

    run_id: str
    run_directory: Path
    started_at: datetime
    ended_at: datetime
    status: RunStatus
    integrity_valid: bool
    integrity_errors: tuple[str, ...] = ()


class AuditCatalog:
    """Discover completed audit runs without trusting filenames as report content."""

    def __init__(self, audit_directory: Path) -> None:
        self._runs = (audit_directory / "runs").resolve()

    def list_runs(self) -> tuple[AuditRunSummary, ...]:
        if not self._runs.exists():
            return ()
        if not self._runs.is_dir() or self._runs.is_symlink():
            message = f"audit runs path is not a regular directory: {self._runs}"
            raise OSError(message)
        summaries = [summary for path in self._runs.iterdir() if (summary := self._load(path))]
        return tuple(sorted(summaries, key=lambda item: item.started_at, reverse=True))

    def find(self, run_id: str) -> AuditRunSummary | None:
        return next((item for item in self.list_runs() if item.run_id == run_id), None)

    def _load(self, path: Path) -> AuditRunSummary | None:
        manifest_path = path / "manifest.json"
        if not path.is_dir() or path.is_symlink():
            return None
        timestamp, separator, run_id = path.name.partition("-")
        if not separator or _RUN_ID.fullmatch(run_id) is None:
            return None
        if not manifest_path.is_file():
            return self._orphaned(path, timestamp, run_id)
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        manifest_errors = verify_manifest(path, manifest)
        event_errors = verify_event_chain(path / "run.jsonl")
        errors = (*manifest_errors, *event_errors)
        return AuditRunSummary(
            run_id=run_id,
            run_directory=path,
            started_at=manifest.start_time,
            ended_at=manifest.end_time,
            status=manifest.final_status,
            integrity_valid=not errors,
            integrity_errors=errors,
        )

    def _orphaned(
        self,
        path: Path,
        timestamp: str,
        run_id: str,
    ) -> AuditRunSummary | None:
        """Surface interrupted runs instead of silently hiding missing terminal manifests."""

        try:
            started_at = datetime.strptime(timestamp, _RUN_TIMESTAMP).replace(tzinfo=UTC)
        except ValueError:
            return None
        return AuditRunSummary(
            run_id=run_id,
            run_directory=path,
            started_at=started_at,
            ended_at=started_at,
            status=RunStatus.INDETERMINATE,
            integrity_valid=False,
            integrity_errors=("terminal manifest is missing; the process may have stopped",),
        )
