"""Portable best-effort restriction of sensitive local files and directories."""

import os
from pathlib import Path


def restrict_permissions(path: Path, mode: int) -> None:
    """Apply POSIX modes where they are meaningful; preserve Windows ACLs unchanged."""

    if os.name == "posix":
        path.chmod(mode)


def directory_is_accessible(path: Path) -> bool:
    """Probe directory enumeration without creating or changing anything."""

    if not path.exists():
        return True
    try:
        next(path.iterdir(), None)
    except OSError:
        return False
    return True
