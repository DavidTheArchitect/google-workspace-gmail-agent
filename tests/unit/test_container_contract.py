"""Static safety contracts for the optional container distribution."""

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_readiness_http_client_is_available_in_the_runtime_image() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert "httpx2==2.5.0" in project["dependencies"]
    assert "httpx2==2.5.0" not in project["optional-dependencies"]["dev"]


def test_container_runtime_is_non_root_and_plan_only() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "USER compliance-agent" in dockerfile
    assert "CA_RUN_MODE=plan_only" in dockerfile
    assert "CA_CONSOLE_BIND_HOST=0.0.0.0" in dockerfile
    assert "/app/.venv /app/.venv" in dockerfile
    assert 'PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert 'ENTRYPOINT ["compliance-agent"]' in dockerfile
    assert 'CMD ["console", "--no-open"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_compose_publishes_only_to_host_loopback_and_persists_state() -> None:
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${CA_CONSOLE_PORT:-8765}:${CA_CONSOLE_PORT:-8765}"' in compose
    assert '"0.0.0.0:${CA_CONSOLE_PORT' not in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "gmail-agent-config:/config" in compose
    assert "gmail-agent-audit:/data/audit" in compose
    assert "gmail-agent-state:/data/state" in compose
    assert "GMAIL_AGENT_OLLAMA_BASE_URL" in compose
    assert "http://host.docker.internal:11434/v1" in compose
    assert "${CA_OLLAMA_BASE_URL:-" not in compose


def test_container_workflow_validates_pull_requests_and_publishes_main() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "packages: write" in workflow
    assert "ghcr.io/${{ github.repository }}" in workflow
    assert "push: ${{ github.event_name == 'push' }}" in workflow
    assert "Smoke-test pull request image" in workflow
