"""Run manifest creation and artifact integrity verification."""

import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import Field

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.status import RunStatus


class ArtifactDigest(FrozenModel):
    """One finalized audit artifact digest."""

    path: str
    sha256: str
    size_bytes: int = Field(ge=0)


class RunManifest(FrozenModel):
    """Versions, timing, terminal status, and artifact integrity data."""

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
    operating_system: str
    start_time: datetime
    end_time: datetime
    final_status: RunStatus
    artifacts: tuple[ArtifactDigest, ...]


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
        content = resolved.read_bytes()
        digests.append(
            ArtifactDigest(
                path=resolved.relative_to(root).as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
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
