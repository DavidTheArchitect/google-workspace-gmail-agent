"""Small, allow-listed updates to the local dotenv configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from compliance_agent.domain.normalization import normalize_domain, normalize_email

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_EDITABLE_KEYS = frozenset(
    {
        "CA_EXPECTED_ADMIN_EMAIL",
        "CA_EXPECTED_WORKSPACE_DOMAIN",
    }
)
_ASSIGNMENT = re.compile(r"^\s*#?\s*(CA_[A-Z0-9_]+)\s*=.*$")


@dataclass(frozen=True, slots=True)
class LocalConfigurationStore:
    """Persist the two non-secret Google identity expectations safely."""

    path: Path

    def save_google_identities(
        self,
        administrator_email: str,
        workspace_domain: str,
    ) -> tuple[str, str]:
        """Validate, normalize, and save the expected Google identities."""

        normalized_email = normalize_email(administrator_email)
        normalized_domain = normalize_domain(workspace_domain)
        self.update(
            {
                "CA_EXPECTED_ADMIN_EMAIL": normalized_email,
                "CA_EXPECTED_WORKSPACE_DOMAIN": normalized_domain,
            }
        )
        return normalized_email, normalized_domain

    def update(self, values: Mapping[str, str]) -> None:
        """Atomically update an allow-listed set of dotenv assignments."""

        unsupported = set(values) - _EDITABLE_KEYS
        if unsupported:
            names = ", ".join(sorted(unsupported))
            message = f"configuration keys cannot be edited here: {names}"
            raise ValueError(message)
        if self.path.is_symlink():
            message = "the local configuration file cannot be a symbolic link"
            raise ValueError(message)

        existing = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        newline = "\r\n" if "\r\n" in existing else "\n"
        remaining = dict(values)
        output: list[str] = []
        written: set[str] = set()

        for line in existing.splitlines():
            match = _ASSIGNMENT.fullmatch(line)
            key = match.group(1) if match else None
            if key not in remaining:
                output.append(line)
                continue
            if key not in written:
                output.append(f"{key}={remaining[key]}")
                written.add(key)

        missing = [key for key in remaining if key not in written]
        if missing and output and output[-1]:
            output.append("")
        output.extend(f"{key}={remaining[key]}" for key in missing)
        content = newline.join(output) + newline

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8", newline="")
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
