"""Install a checksum-verified project-local Node runtime for Reflex."""

from __future__ import annotations

import hashlib
import shutil
import urllib.request
import zipfile
from pathlib import Path

NODE_VERSION = "v22.22.3"
ARCHIVE_NAME = f"node-{NODE_VERSION}-win-x64.zip"
BASE_URL = f"https://nodejs.org/dist/{NODE_VERSION}"


def install(repository: Path) -> Path:
    """Return the Node executable, downloading only when it is absent."""

    install_dir = repository / ".node" / f"node-{NODE_VERSION}-win-x64"
    executable = install_dir / "node.exe"
    if executable.is_file():
        return executable
    archive = repository / ".node-cache.zip"
    with urllib.request.urlopen(f"{BASE_URL}/SHASUMS256.txt", timeout=30) as response:  # noqa: S310
        checksums = response.read().decode("utf-8")
    expected = next(
        line.split()[0] for line in checksums.splitlines() if line.endswith(ARCHIVE_NAME)
    )
    with (
        urllib.request.urlopen(f"{BASE_URL}/{ARCHIVE_NAME}", timeout=60) as response,  # noqa: S310
        archive.open("wb") as destination,
    ):
        shutil.copyfileobj(response, destination)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        message = "project-local Node archive checksum mismatch"
        raise RuntimeError(message)
    (repository / ".node").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(repository / ".node")
    if not executable.is_file():
        message = "project-local Node extraction did not produce node.exe"
        raise RuntimeError(message)
    archive.unlink(missing_ok=True)
    return executable


def main() -> None:
    """Install beneath the repository containing this script."""

    executable = install(Path(__file__).resolve().parents[1])
    print(executable)


if __name__ == "__main__":
    main()
