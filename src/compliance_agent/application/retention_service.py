"""Explicit, fail-closed audit retention planning and application."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from compliance_agent.exceptions import AuditRetentionFailure
from compliance_agent.infrastructure.clock import Clock
from compliance_agent.schemas.base import FrozenModel

_RUN_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_RUN_ID_HEX_LENGTH = 32


class RetentionCandidate(FrozenModel):
    """One exact run directory eligible for retention deletion."""

    path: Path
    created_at: datetime


class AuditRetentionService:
    """Plan retention by default and delete only exact revalidated run directories."""

    def __init__(self, audit_directory: Path, clock: Clock, retention_days: int) -> None:
        self._runs_directory = (audit_directory / "runs").resolve()
        self._clock = clock
        self._retention_days = retention_days

    def find_expired(self) -> tuple[RetentionCandidate, ...]:
        """Return old, convention-named, non-symlink run directories without deleting them."""

        if not self._runs_directory.exists():
            return ()
        if not self._runs_directory.is_dir() or self._runs_directory.is_symlink():
            message = f"audit runs path is not a regular directory: {self._runs_directory}"
            raise AuditRetentionFailure(message)
        cutoff = self._clock.now() - timedelta(days=self._retention_days)
        candidates: list[RetentionCandidate] = []
        for path in self._runs_directory.iterdir():
            created_at = _run_created_at(path.name)
            if (
                created_at is not None
                and created_at < cutoff
                and path.is_dir()
                and not path.is_symlink()
            ):
                candidates.append(RetentionCandidate(path=path, created_at=created_at))
        return tuple(sorted(candidates, key=lambda candidate: candidate.path.name))

    def delete_expired(
        self,
        candidates: tuple[RetentionCandidate, ...],
    ) -> tuple[Path, ...]:
        """Delete only candidates still matching the exact retention plan and root."""

        paths = [candidate.path for candidate in candidates]
        if len(paths) != len(set(paths)):
            message = "audit retention plan contains duplicate candidates"
            raise AuditRetentionFailure(message)
        invalid = next(
            (candidate for candidate in candidates if not self._is_still_safe(candidate)),
            None,
        )
        if invalid is not None:
            message = f"audit retention candidate failed revalidation: {invalid.path}"
            raise AuditRetentionFailure(message)
        deleted: list[Path] = []
        for candidate in candidates:
            path = candidate.path
            try:
                shutil.rmtree(path)
            except OSError as error:
                message = f"could not delete expired audit run: {path}"
                raise AuditRetentionFailure(message) from error
            deleted.append(path)
        return tuple(deleted)

    def _is_still_safe(self, candidate: RetentionCandidate) -> bool:
        path = candidate.path
        try:
            resolved_path = path.resolve(strict=True)
        except OSError:
            return False
        created_at = _run_created_at(path.name)
        cutoff = self._clock.now() - timedelta(days=self._retention_days)
        return (
            path == resolved_path
            and resolved_path.parent == self._runs_directory
            and path.exists()
            and path.is_dir()
            and not path.is_symlink()
            and created_at == candidate.created_at
            and candidate.created_at < cutoff
        )


def _run_created_at(name: str) -> datetime | None:
    timestamp, separator, run_id = name.partition("-")
    valid_run_id = len(run_id) == _RUN_ID_HEX_LENGTH and all(
        character in "0123456789abcdef" for character in run_id
    )
    if not separator or not valid_run_id:
        return None
    try:
        parsed = datetime.strptime(timestamp, _RUN_TIMESTAMP_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)
