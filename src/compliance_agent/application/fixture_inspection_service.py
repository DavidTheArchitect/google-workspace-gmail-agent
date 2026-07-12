"""Inert sanitized Admin UI evidence inspection for contract promotion."""

import hashlib
import json
import re
from pathlib import Path

from compliance_agent.schemas.base import FrozenModel, Sha256Digest

_REQUIRED_FILES = ("metadata.json", "page.html", "aria.txt")
_PROHIBITED_PATTERNS = (
    re.compile(r"BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?i)(?:cookie|authorization|password)\s*[:=]"),
)
_AUTH_STATES = {"login_required", "account_chooser", "two_step_verification"}


class FixtureInspection(FrozenModel):
    """Hashes and closed safety findings for one sanitized fixture directory."""

    valid: bool
    file_hashes: dict[str, Sha256Digest]
    errors: tuple[str, ...] = ()


def inspect_fixture_directory(directory: Path) -> FixtureInspection:
    """Inspect fixture bytes as inert text without rendering or executing content."""

    root = directory.resolve()
    errors: list[str] = []
    hashes: dict[str, str] = {}
    if not root.is_dir() or root.is_symlink():
        return FixtureInspection(valid=False, file_hashes={}, errors=("fixture_not_regular",))
    for filename in _REQUIRED_FILES:
        path = root / filename
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing_{filename}")
            continue
        content = path.read_bytes()
        hashes[filename] = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8")
        except UnicodeError:
            errors.append(f"invalid_utf8_{filename}")
            continue
        if any(pattern.search(text) for pattern in _PROHIBITED_PATTERNS):
            errors.append(f"prohibited_sensitive_pattern_{filename}")
    metadata = _metadata(root / "metadata.json", errors)
    if metadata.get("detected_state") in _AUTH_STATES:
        errors.append("authentication_page_capture_prohibited")
    return FixtureInspection(valid=not errors, file_hashes=hashes, errors=tuple(errors))


def _metadata(path: Path, errors: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        errors.append("invalid_metadata")
        return {}
    if not isinstance(value, dict):
        errors.append("metadata_not_object")
        return {}
    return value
