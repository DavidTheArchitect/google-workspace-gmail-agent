"""Pure presentation projection for console run history."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from compliance_agent.schemas.operations import ConsoleRun, RunPhase

TimelineTone = Literal["ok", "active", "blocked", "uncertain"]
_SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    phase: RunPhase
    at: datetime
    duration_label: str
    error_code: str | None
    tone: TimelineTone


def build_timeline(run: ConsoleRun, now: datetime) -> tuple[TimelineEntry, ...]:
    """Build deterministic entries with precomputed duration and tone labels."""

    history = run.history
    if not history:
        return (
            TimelineEntry(
                phase=run.phase,
                at=run.updated_at,
                duration_label="0s",
                error_code=run.error_code,
                tone=_tone(run.phase, active=False),
            ),
        )
    entries: list[TimelineEntry] = []
    for index, transition in enumerate(history):
        end = history[index + 1].at if index + 1 < len(history) else now
        active = index == len(history) - 1 and run.phase in {
            RunPhase.PLANNING,
            RunPhase.PREFLIGHT,
            RunPhase.AWAITING_APPROVAL,
            RunPhase.EXECUTING,
            RunPhase.VERIFYING,
        }
        entries.append(
            TimelineEntry(
                phase=transition.phase,
                at=transition.at,
                duration_label=_duration_label(max(0, int((end - transition.at).total_seconds()))),
                error_code=transition.error_code,
                tone=_tone(transition.phase, active=active),
            )
        )
    return tuple(entries)


def _duration_label(seconds: int) -> str:
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds}s"
    minutes, remaining = divmod(seconds, _SECONDS_PER_MINUTE)
    if minutes < _SECONDS_PER_MINUTE:
        return f"{minutes}m {remaining:02d}s"
    hours, minutes = divmod(minutes, _SECONDS_PER_MINUTE)
    return f"{hours}h {minutes:02d}m"


def _tone(phase: RunPhase, *, active: bool) -> TimelineTone:
    if phase == RunPhase.INTERRUPTED:
        return "uncertain"
    if phase in {RunPhase.BLOCKED, RunPhase.CANCELLED}:
        return "blocked"
    return "active" if active else "ok"
