"""Hash-chained event and artifact persistence for one protected run directory."""

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePath

from compliance_agent.exceptions import AuditWriteFailure
from compliance_agent.schemas.events import AuditEvent


class RunAuditWriter:
    """Persist already-created audit facts without interpreting their meaning."""

    def __init__(self, run_directory: Path) -> None:
        self.run_directory = run_directory.resolve()
        self.run_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.run_directory.chmod(0o700)
        self._event_path = self.run_directory / "run.jsonl"
        self._last_sequence = 0
        self._last_hash: str | None = None
        self._run_id: str | None = None

    def append(self, event: AuditEvent) -> AuditEvent:
        """Append a sequence-checked event with a computed previous/current hash chain."""

        if event.sequence != self._last_sequence + 1:
            message = f"expected audit sequence {self._last_sequence + 1}, got {event.sequence}"
            raise AuditWriteFailure(message)
        if self._run_id is not None and event.run_id != self._run_id:
            message = f"audit event run ID changed from {self._run_id} to {event.run_id}"
            raise AuditWriteFailure(message)
        if event.previous_event_hash not in {None, self._last_hash}:
            message = "event supplied a previous hash that does not match the run chain"
            raise AuditWriteFailure(message)
        unhashed = event.model_copy(
            update={"previous_event_hash": self._last_hash, "event_hash": None}
        )
        payload = _canonical_event_json(unhashed)
        event_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        finalized = unhashed.model_copy(update={"event_hash": event_hash})
        try:
            with self._event_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(finalized.model_dump_json(exclude_none=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            message = f"could not append audit event to {self._event_path}"
            raise AuditWriteFailure(message) from error
        self._last_sequence = finalized.sequence
        self._last_hash = event_hash
        self._run_id = finalized.run_id
        return finalized

    @property
    def next_sequence(self) -> int:
        """Return the next required event sequence without exposing mutable writer state."""

        return self._last_sequence + 1

    def write_text(self, relative_path: str, content: str) -> Path:
        """Atomically write one run-relative UTF-8 artifact without path traversal."""

        target = self._safe_target(relative_path)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}."
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(target)
            target.chmod(0o600)
        except OSError as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            message = f"could not write audit artifact {relative_path}"
            raise AuditWriteFailure(message) from error
        return target

    def _safe_target(self, relative_path: str) -> Path:
        pure_path = PurePath(relative_path)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            message = f"audit artifact path must remain run-relative: {relative_path}"
            raise AuditWriteFailure(message)
        target = (self.run_directory / pure_path).resolve()
        if self.run_directory not in target.parents:
            message = f"audit artifact escaped the run directory: {relative_path}"
            raise AuditWriteFailure(message)
        return target


def _canonical_event_json(event: AuditEvent) -> str:
    value = event.model_dump(mode="json", exclude={"event_hash"}, exclude_none=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_event_chain(event_path: Path) -> tuple[str, ...]:
    """Return deterministic errors for a malformed or tampered JSONL event chain."""

    errors: list[str] = []
    expected_sequence = 1
    previous_hash: str | None = None
    try:
        lines = event_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return (f"event stream cannot be read: {error}",)
    if not lines:
        return ("event stream is empty",)
    expected_run_id: str | None = None
    for line_number, line in enumerate(lines, start=1):
        try:
            event = AuditEvent.model_validate_json(line)
        except ValueError as error:
            errors.append(f"line {line_number}: invalid event: {error}")
            continue
        if expected_run_id is None:
            expected_run_id = event.run_id
        elif event.run_id != expected_run_id:
            errors.append(f"line {line_number}: run ID mismatch")
        if event.sequence != expected_sequence:
            errors.append(f"line {line_number}: unexpected sequence {event.sequence}")
        if event.previous_event_hash != previous_hash:
            errors.append(f"line {line_number}: previous hash mismatch")
        unhashed = event.model_copy(update={"event_hash": None})
        expected_hash = hashlib.sha256(_canonical_event_json(unhashed).encode("utf-8")).hexdigest()
        if event.event_hash != expected_hash:
            errors.append(f"line {line_number}: event hash mismatch")
        expected_sequence = event.sequence + 1
        previous_hash = event.event_hash
    return tuple(errors)
