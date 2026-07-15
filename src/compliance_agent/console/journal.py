"""Best-effort console-run projections; hash-chained audit evidence stays authoritative.

Sessions, CSRF and launch tokens, and pending approvals are deliberately never persisted.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from compliance_agent.infrastructure.protected_json import ProtectedJsonStore
from compliance_agent.schemas.operations import ConsoleRun, PhaseTransition, RunPhase

_LOGGER = logging.getLogger(__name__)
_TERMINAL_PHASES = frozenset(
    {
        RunPhase.PREVIEW_READY,
        RunPhase.COMPLETED,
        RunPhase.BLOCKED,
        RunPhase.CANCELLED,
        RunPhase.INTERRUPTED,
    }
)
_TERMINAL_LIMIT = 100


class ConsoleRunSnapshot(BaseModel):
    """Versioned durable projection for one console run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    run: ConsoleRun


class _JournalEnvelope(BaseModel):
    """Loose envelope lets future schema versions survive old-console saves."""

    model_config = ConfigDict(frozen=True, extra="allow")

    schema_version: str
    run: dict[str, Any]


class ConsoleRunJournal:
    """Load and atomically replace recoverable console-run projections."""

    def __init__(self, state_directory: Path) -> None:
        self._store = ProtectedJsonStore(state_directory / "console-runs.json")
        self._unknown: tuple[_JournalEnvelope, ...] = ()
        self._writable = True

    def load(self, now: datetime) -> tuple[ConsoleRun, ...]:
        """Restore known snapshots and fail closed without touching corrupt input."""

        try:
            envelopes = self._store.load(_JournalEnvelope)
        except OSError:
            _LOGGER.warning(
                "Console run journal is unavailable; persistence is disabled",
                exc_info=True,
            )
            self._writable = False
            return ()
        except (TypeError, UnicodeError, ValueError):
            _LOGGER.warning("Console run journal is corrupt; leaving it untouched", exc_info=True)
            self._writable = False
            return ()
        self._unknown = tuple(item for item in envelopes if item.schema_version != "1")
        restored: list[ConsoleRun] = []
        for envelope in envelopes:
            if envelope.schema_version != "1":
                continue
            try:
                snapshot = ConsoleRunSnapshot.model_validate(envelope.model_dump())
            except ValidationError:
                _LOGGER.warning("Skipping invalid console run snapshot", exc_info=True)
                continue
            restored.append(_restore_run(snapshot.run, now))
        return tuple(restored)

    def save(self, runs: tuple[ConsoleRun, ...]) -> None:
        """Persist active runs plus the newest bounded terminal history."""

        if not self._writable:
            return
        active = tuple(run for run in runs if run.phase not in _TERMINAL_PHASES)
        terminal = tuple(run for run in runs if run.phase in _TERMINAL_PHASES)[:_TERMINAL_LIMIT]
        snapshots = tuple(
            _JournalEnvelope.model_validate(ConsoleRunSnapshot(run=run).model_dump(mode="json"))
            for run in (*active, *terminal)
        )
        self._store.save((*self._unknown, *snapshots))


def _restore_run(run: ConsoleRun, now: datetime) -> ConsoleRun:
    if run.phase in {RunPhase.PLANNING, RunPhase.PREFLIGHT}:
        return _downgrade(run, RunPhase.BLOCKED, "console_restarted", now)
    if run.phase == RunPhase.AWAITING_APPROVAL:
        return _downgrade(
            run,
            RunPhase.PLAN_READY,
            "approval_expired",
            now,
            preview=None,
            result=None,
        )
    if run.phase in {RunPhase.EXECUTING, RunPhase.VERIFYING}:
        return _downgrade(
            run,
            RunPhase.INTERRUPTED,
            "console_restarted_execution_uncertain",
            now,
        )
    return run


def _downgrade(
    run: ConsoleRun,
    phase: RunPhase,
    error_code: str,
    now: datetime,
    **updates: object,
) -> ConsoleRun:
    transition = PhaseTransition(phase=phase, at=now, error_code=error_code)
    return run.model_copy(
        update={
            "phase": phase,
            "updated_at": now,
            "error_code": error_code,
            "history": (*run.history, transition),
            **updates,
        }
    )
