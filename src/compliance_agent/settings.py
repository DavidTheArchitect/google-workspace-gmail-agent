"""Validated deployment settings loaded only at the application boundary."""

import os
import stat
import unicodedata
from pathlib import Path
from typing import Self

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from compliance_agent.domain.normalization import normalize_domain, normalize_email

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_PREFIX_CHARACTERS = 100
_MAX_MODEL_TAG_CHARACTERS = 200


class Settings(BaseSettings):
    """Environment-backed settings with no unattended-approval escape hatch."""

    model_config = SettingsConfigDict(
        env_prefix="CA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    ollama_base_url: HttpUrl = HttpUrl("http://localhost:11434/v1")
    ollama_model: str = "gemma3:12b"
    profile_dir: Path = Path.home() / ".compliance_agent" / "browser-profile"
    audit_dir: Path = Path.home() / ".compliance_agent" / "audit"
    state_dir: Path = Path.home() / ".compliance_agent" / "state"
    headless: bool = False
    dry_run: bool = True
    plan_only: bool = True
    llm_max_retries: int = Field(default=3, ge=0, le=3)
    llm_temperature: float = Field(default=0, ge=0, le=0)
    navigation_timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    action_timeout_ms: int = Field(default=10_000, ge=1_000, le=60_000)
    save_timeout_ms: int = Field(default=15_000, ge=1_000, le=120_000)
    audit_retention_days: int = Field(default=90, ge=1, le=3_650)
    managed_resource_prefix: str = "[Compliance Agent]"
    expected_admin_email: str = ""
    expected_workspace_domain: str = ""
    google_admin_base_url: HttpUrl = HttpUrl("https://admin.google.com")
    gmail_settings_url: HttpUrl = HttpUrl("https://admin.google.com/ac/apps/gmail")

    @model_validator(mode="after")
    def enforce_live_safety(self) -> Self:
        normalized_paths = _validated_sensitive_paths(self.sensitive_directories)
        object.__setattr__(self, "profile_dir", normalized_paths[0])
        object.__setattr__(self, "audit_dir", normalized_paths[1])
        object.__setattr__(self, "state_dir", normalized_paths[2])
        prefix = _visible_setting(
            self.managed_resource_prefix,
            maximum_characters=_MAX_PREFIX_CHARACTERS,
            label="managed resource prefix",
        )
        object.__setattr__(self, "managed_resource_prefix", prefix)
        model = _visible_setting(
            self.ollama_model,
            maximum_characters=_MAX_MODEL_TAG_CHARACTERS,
            label="Ollama model tag",
        )
        object.__setattr__(self, "ollama_model", model)
        _validate_service_urls(self)
        administrator_email, workspace_domain = _normalized_identities(
            self.expected_admin_email,
            self.expected_workspace_domain,
        )
        object.__setattr__(self, "expected_admin_email", administrator_email)
        object.__setattr__(self, "expected_workspace_domain", workspace_domain)
        if not self.dry_run and not self.plan_only:
            if self.headless:
                message = "live mutations require a headed browser"
                raise ValueError(message)
            if not self.expected_admin_email.strip():
                message = "live mutations require CA_EXPECTED_ADMIN_EMAIL"
                raise ValueError(message)
            if not self.expected_workspace_domain.strip():
                message = "live mutations require CA_EXPECTED_WORKSPACE_DOMAIN"
                raise ValueError(message)
        return self

    @property
    def sensitive_directories(self) -> tuple[Path, Path, Path]:
        """Return the profile, audit, and state paths in validation order."""

        return self.profile_dir, self.audit_dir, self.state_dir


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        message = f"sensitive path must be absolute: {path}"
        raise ValueError(message)
    if expanded.is_symlink():
        message = f"sensitive path cannot be a symbolic link: {path}"
        raise ValueError(message)
    resolved = expanded.resolve()
    if resolved.exists() and not resolved.is_dir():
        message = f"sensitive path must be a directory: {path}"
        raise ValueError(message)
    if os.name == "posix" and resolved.exists():
        permissions = stat.S_IMODE(resolved.stat().st_mode)
        if permissions & 0o077:
            message = f"sensitive directory permissions are too broad: {path}"
            raise ValueError(message)
    return resolved


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _validated_sensitive_paths(paths: tuple[Path, Path, Path]) -> tuple[Path, Path, Path]:
    normalized = (_absolute(paths[0]), _absolute(paths[1]), _absolute(paths[2]))
    if any(
        _paths_overlap(first, second)
        for index, first in enumerate(normalized)
        for second in normalized[index + 1 :]
    ):
        message = "profile, state, and audit directories must be distinct and non-overlapping"
        raise ValueError(message)
    return normalized


def _contains_control_or_format_character(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)


def _visible_setting(value: str, *, maximum_characters: int, label: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum_characters
        or _contains_control_or_format_character(normalized)
    ):
        message = f"{label} must be 1-{maximum_characters} visible characters"
        raise ValueError(message)
    return normalized


def _validate_service_urls(settings: Settings) -> None:
    if settings.ollama_base_url.host not in _LOOPBACK_HOSTS:
        message = "CA_OLLAMA_BASE_URL must use a loopback host"
        raise ValueError(message)
    _require_google_admin_url(settings.google_admin_base_url, "CA_GOOGLE_ADMIN_BASE_URL")
    _require_google_admin_url(settings.gmail_settings_url, "CA_GMAIL_SETTINGS_URL")


def _normalized_identities(administrator_email: str, workspace_domain: str) -> tuple[str, str]:
    administrator_email = administrator_email.strip()
    workspace_domain = workspace_domain.strip()
    if administrator_email:
        administrator_email = normalize_email(administrator_email)
    if workspace_domain:
        workspace_domain = normalize_domain(workspace_domain)
    return administrator_email, workspace_domain


def _require_google_admin_url(url: HttpUrl, setting_name: str) -> None:
    if url.scheme != "https" or url.host != "admin.google.com":
        message = f"{setting_name} must use https://admin.google.com"
        raise ValueError(message)
