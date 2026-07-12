"""Run manifest creation and artifact integrity verification."""

import hashlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Self

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel, Sha256Digest
from compliance_agent.schemas.status import RunStatus


class ArtifactDigest(FrozenModel):
    """One finalized audit artifact digest."""

    path: str
    sha256: Sha256Digest
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def require_safe_relative_path(self) -> Self:
        path = PurePosixPath(self.path)
        if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in self.path:
            message = "artifact digest path must be a safe relative POSIX path"
            raise ValueError(message)
        return self


class RunManifestMetadata(FrozenModel):
    """Runtime facts captured at run start and injected into finalization."""

    application_version: str
    git_commit: str | None
    dirty_working_tree: bool | None
    python_version: str
    agent_framework_version: str
    playwright_version: str
    browser_version: str | None
    pydantic_version: str
    ollama_version: str | None
    model_tag: str | None
    model_digest: str | None
    ui_contract_digest: Sha256Digest | None = None
    operating_system: str
    start_time: datetime

    @model_validator(mode="after")
    def require_aware_start_time(self) -> Self:
        if self.start_time.tzinfo is None or self.start_time.utcoffset() is None:
            message = "manifest start time must be timezone-aware"
            raise ValueError(message)
        return self


class RunManifest(RunManifestMetadata):
    """Versions, timing, terminal status, and artifact integrity data."""

    end_time: datetime
    final_status: RunStatus
    artifacts: tuple[ArtifactDigest, ...]

    @model_validator(mode="after")
    def validate_timing_and_artifacts(self) -> Self:
        if self.end_time.tzinfo is None or self.end_time.utcoffset() is None:
            message = "manifest end time must be timezone-aware"
            raise ValueError(message)
        if self.end_time < self.start_time:
            message = "manifest end time cannot precede its start time"
            raise ValueError(message)
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            message = "manifest contains duplicate artifact paths"
            raise ValueError(message)
        return self


def digest_artifacts(run_directory: Path) -> tuple[ArtifactDigest, ...]:
    """Hash finalized regular files without following files outside the run directory."""

    root = run_directory.resolve()
    digests: list[ArtifactDigest] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json" or path.is_symlink():
            continue
        resolved = path.resolve()
        if root not in resolved.parents:
            continue
        sha256, size_bytes = _digest_file(resolved)
        digests.append(
            ArtifactDigest(
                path=resolved.relative_to(root).as_posix(),
                sha256=sha256,
                size_bytes=size_bytes,
            )
        )
    return tuple(digests)


def verify_manifest(run_directory: Path, manifest: RunManifest) -> tuple[str, ...]:
    """Return paths whose current bytes disagree with a recorded manifest."""

    current = {digest.path: digest for digest in digest_artifacts(run_directory)}
    expected = {digest.path: digest for digest in manifest.artifacts}
    paths = current.keys() | expected.keys()
    return tuple(
        sorted(
            path
            for path in paths
            if current.get(path) is None
            or expected.get(path) is None
            or current[path].sha256 != expected[path].sha256
            or current[path].size_bytes != expected[path].size_bytes
        )
    )


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    return digest.hexdigest(), size_bytes
