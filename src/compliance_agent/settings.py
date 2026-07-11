"""Validated deployment settings loaded only at the application boundary."""

from pathlib import Path
from typing import Self

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        normalized_paths = tuple(_absolute(path) for path in self.sensitive_directories)
        if len(set(normalized_paths)) != len(normalized_paths):
            message = "profile, state, and audit directories must be distinct"
            raise ValueError(message)
        object.__setattr__(self, "profile_dir", normalized_paths[0])
        object.__setattr__(self, "audit_dir", normalized_paths[1])
        object.__setattr__(self, "state_dir", normalized_paths[2])
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
    return expanded.resolve()
