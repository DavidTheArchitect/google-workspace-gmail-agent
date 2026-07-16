"""Atomic local ownership-registry persistence."""

import os
import tempfile
from pathlib import Path

from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.infrastructure.permissions import restrict_permissions


class OwnershipStore:
    """Persist the one versioned ownership file outside the repository and audit tree."""

    def __init__(self, state_directory: Path) -> None:
        self._state_directory = state_directory.resolve()
        self._path = self._state_directory / "resources.json"

    def load(self) -> OwnershipRegistry:
        """Load validated local evidence or return an empty registry when absent."""

        if not self._path.exists():
            return OwnershipRegistry()
        if self._path.is_symlink():
            message = "ownership registry cannot be a symbolic link"
            raise OSError(message)
        return OwnershipRegistry.model_validate_json(self._path.read_text(encoding="utf-8"))

    def save(self, registry: OwnershipRegistry) -> None:
        """Atomically replace local evidence with a validated registry."""

        self._state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        restrict_permissions(self._state_directory, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._state_directory,
            prefix=".resources.",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(registry.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self._path)
            restrict_permissions(self._path, 0o600)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise
