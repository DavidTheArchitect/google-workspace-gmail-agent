"""Install a checksum-verified project-local Node runtime for Reflex."""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

NODE_VERSION = "v22.22.3"
BASE_URL = f"https://nodejs.org/dist/{NODE_VERSION}"
_CHECKSUM_LINE_PARTS = 2


@dataclass(frozen=True, slots=True)
class NodeDistribution:
    archive_name: str
    directory_name: str
    executable_path: Path


def install(
    repository: Path,
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> Path:
    """Return the Node executable, downloading only when it is absent."""

    distribution = resolve_distribution(
        platform_name=platform_name,
        machine=machine,
    )
    node_root = repository / ".node"
    install_dir = node_root / distribution.directory_name
    executable = install_dir / distribution.executable_path
    if executable.is_file():
        return executable
    node_root.mkdir(parents=True, exist_ok=True)
    cache_dir = repository / ".node-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / distribution.archive_name
    with urllib.request.urlopen(f"{BASE_URL}/SHASUMS256.txt", timeout=30) as response:  # noqa: S310
        checksums = response.read().decode("utf-8")
    expected = _expected_checksum(checksums, distribution.archive_name)
    with (
        urllib.request.urlopen(  # noqa: S310
            f"{BASE_URL}/{distribution.archive_name}",
            timeout=60,
        ) as response,
        archive.open("wb") as destination,
    ):
        shutil.copyfileobj(response, destination)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        message = "project-local Node archive checksum mismatch"
        raise RuntimeError(message)
    with tempfile.TemporaryDirectory(dir=node_root) as temporary:
        extraction_root = Path(temporary)
        _extract_archive(archive, extraction_root)
        extracted = extraction_root / distribution.directory_name
        if not (extracted / distribution.executable_path).is_file():
            message = "project-local Node extraction did not produce the expected executable"
            raise RuntimeError(message)
        if install_dir.exists():
            shutil.rmtree(install_dir)
        shutil.move(str(extracted), install_dir)
    if not executable.is_file():
        message = "project-local Node installation did not produce the expected executable"
        raise RuntimeError(message)
    archive.unlink(missing_ok=True)
    return executable


def resolve_distribution(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> NodeDistribution:
    """Resolve one supported official Node distribution for the current machine."""

    effective_platform = sys.platform if platform_name is None else platform_name
    effective_machine = platform.machine().lower() if machine is None else machine.lower()
    if effective_platform == "win32" and effective_machine in {"amd64", "x86_64"}:
        target = "win-x64"
        extension = "zip"
        executable = Path("node.exe")
    elif effective_platform.startswith("linux") and effective_machine in {"amd64", "x86_64"}:
        target = "linux-x64"
        extension = "tar.xz"
        executable = Path("bin/node")
    elif effective_platform.startswith("linux") and effective_machine in {"aarch64", "arm64"}:
        target = "linux-arm64"
        extension = "tar.xz"
        executable = Path("bin/node")
    else:
        message = f"unsupported Node platform: {effective_platform}/{effective_machine}"
        raise RuntimeError(message)
    directory_name = f"node-{NODE_VERSION}-{target}"
    return NodeDistribution(
        archive_name=f"{directory_name}.{extension}",
        directory_name=directory_name,
        executable_path=executable,
    )


def _expected_checksum(checksums: str, archive_name: str) -> str:
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == _CHECKSUM_LINE_PARTS and parts[1].lstrip("*") == archive_name:
            return parts[0]
    message = f"Node checksum manifest does not contain {archive_name}"
    raise RuntimeError(message)


def _extract_archive(archive: Path, destination: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            _extract_zip(bundle, destination)
        return
    with tarfile.open(archive, mode="r:xz") as bundle:
        for member in bundle.getmembers():
            bundle.extract(member, destination, filter="data")


def _extract_zip(bundle: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in bundle.infolist():
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(root):
            message = "Node archive contains an unsafe path"
            raise RuntimeError(message)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def main() -> None:
    """Install beneath the repository containing this script."""

    executable = install(Path(__file__).resolve().parents[1])
    print(executable)


if __name__ == "__main__":
    main()
