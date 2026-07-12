"""Collect runtime version facts for an injected audit manifest."""

import importlib.metadata
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from compliance_agent.audit.manifest import RunManifestMetadata
from compliance_agent.schemas.base import Sha256Digest
from compliance_agent.settings import Settings
from compliance_agent.version import __version__


def collect_manifest_metadata(
    start_time: datetime,
    settings: Settings,
    repository: Path | None = None,
    ui_contract_digest: Sha256Digest | None = None,
) -> RunManifestMetadata:
    """Return available runtime facts without making missing optional tools fatal."""

    git_commit, dirty_working_tree = _git_facts(repository or Path.cwd())
    return RunManifestMetadata(
        application_version=__version__,
        git_commit=git_commit,
        dirty_working_tree=dirty_working_tree,
        python_version=platform.python_version(),
        agent_framework_version=_package_version("agent-framework-core"),
        playwright_version=_package_version("playwright"),
        browser_version=None,
        pydantic_version=_package_version("pydantic"),
        ollama_version=None,
        model_tag=settings.ollama_model,
        model_digest=None,
        ui_contract_digest=ui_contract_digest,
        operating_system=platform.platform(),
        start_time=start_time,
    )


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _git_facts(repository: Path) -> tuple[str | None, bool | None]:
    executable = shutil.which("git")
    if executable is None:
        return None, None
    commit = _run_git(executable, repository, "rev-parse", "HEAD")
    status = _run_git(executable, repository, "status", "--porcelain")
    dirty = bool(status) if status is not None else None
    return commit, dirty


def _run_git(executable: str, repository: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - executable and arguments are fixed internally.
            [executable, "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
