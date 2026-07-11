"""Cross-platform exclusive run lock acquired before browser launch."""

import json
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import TextIO

import portalocker

from compliance_agent.exceptions import RunLockUnavailable


class ProcessLock:
    """Own an OS-backed exclusive lock and a human-readable lock record."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        started_at: datetime,
        application_version: str,
    ) -> None:
        self._path = path
        self._run_id = run_id
        self._started_at = started_at
        self._application_version = application_version
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        """Acquire the lock non-blockingly or fail before any browser is opened."""

        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            _lock_file(handle)
        except (OSError, portalocker.LockException) as error:
            handle.close()
            message = f"another run owns {self._path}; inspect the lock record before recovery"
            raise RunLockUnavailable(message) from error
        self._handle = handle
        record = {
            "process_id": os.getpid(),
            "run_id": self._run_id,
            "start_time": self._started_at.isoformat(),
            "hostname": socket.gethostname(),
            "application_version": self._application_version,
        }
        handle.seek(0)
        handle.truncate()
        json.dump(record, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())

    def release(self) -> None:
        """Release the OS lock; the record remains for diagnostics."""

        if self._handle is None:
            return
        _unlock_file(self._handle)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.release()


def _lock_file(handle: TextIO) -> None:
    portalocker.lock(
        handle,
        portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING,
    )


def _unlock_file(handle: TextIO) -> None:
    portalocker.unlock(handle)
