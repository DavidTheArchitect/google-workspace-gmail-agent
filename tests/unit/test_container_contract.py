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
    assert "CA_OLLAMA_BASE_URL=http://ollama:11434/v1" in dockerfile
    assert "CA_OLLAMA_MODEL=gemma4:12b" in dockerfile
    assert "/app/.venv /app/.venv" in dockerfile
    assert 'PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert "FROM node:22.22.0-bookworm-slim AS node" in dockerfile
    assert "reflex export --env prod --frontend-only --no-zip" in dockerfile
    assert "/app/assets /app/assets" in dockerfile
    assert "/app/gmail_admin_agent /app/gmail_admin_agent" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/container-entrypoint"]' in dockerfile
    assert 'CMD ["gmail-agent", "console"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:{port}/" in dockerfile


def test_compose_runs_an_internal_persistent_ollama_service() -> None:
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "image: ghcr.io/davidthearchitect/google-workspace-gmail-agent:latest" in compose
    assert "GMAIL_AGENT_IMAGE" not in compose
    assert compose.count("image: ollama/ollama:latest") == 2
    assert "ollama-models:/root/.ollama" in compose
    assert 'test: ["CMD", "ollama", "list"]' in compose
    assert 'OLLAMA_BASE_URL: "${OLLAMA_BASE_URL:-http://ollama:11434/v1}"' in compose
    assert 'OLLAMA_MODEL: "${OLLAMA_MODEL:-gemma4:12b}"' in compose
    assert 'CA_OLLAMA_BASE_URL: "${OLLAMA_BASE_URL:-http://ollama:11434/v1}"' in compose
    assert 'CA_OLLAMA_MODEL: "${OLLAMA_MODEL:-gemma4:12b}"' in compose
    assert 'CA_LLM_REQUEST_TIMEOUT_SECONDS: "${LLM_REQUEST_TIMEOUT_SECONDS:-600}"' in compose
    assert 'CA_GROUP_CHAT_TIMEOUT_SECONDS: "${GROUP_CHAT_TIMEOUT_SECONDS:-1800}"' in compose
    assert "condition: service_healthy" in compose
    assert "condition: service_completed_successfully" in compose
    assert 'restart: "no"' in compose
    assert '"11434:11434"' not in compose
    assert "host.docker.internal" not in compose


def test_compose_model_initializer_is_idempotent_and_fail_closed() -> None:
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert 'if ollama show "$$model"' in compose
    assert 'ollama pull "$$model"' in compose
    assert "OLLAMA_INIT_MAX_ATTEMPTS" in compose
    assert "Ollama did not become ready" in compose
    assert "already available; skipping download" in compose


def test_compose_publishes_only_the_application_to_host_loopback_and_persists_state() -> None:
    compose = (_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${CA_CONSOLE_PORT:-8765}:${CA_CONSOLE_PORT:-8765}"' in compose
    assert '"0.0.0.0:${CA_CONSOLE_PORT' not in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "gmail-agent-config:/config" in compose
    assert "gmail-agent-audit:/data/audit" in compose
    assert "gmail-agent-reflex-cache:/var/lib/gmail-agent-reflex" in compose
    assert "NPM_CONFIG_CACHE: /var/lib/gmail-agent-reflex/.npm" in compose
    assert "REFLEX_DIR: /var/lib/gmail-agent-reflex/.reflex" in compose
    assert "gmail-agent-state:/data/state" in compose
    assert 'CODESPACES: "${CODESPACES:-false}"' in compose
    assert "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN" in compose
    assert "gemma4:12b" in compose
    assert "${CA_OLLAMA_BASE_URL:-" not in compose


def test_optional_gpu_override_only_accelerates_ollama() -> None:
    override = (_ROOT / "compose.gpu.yaml").read_text(encoding="utf-8")

    assert "ollama:" in override
    assert "gpus: all" in override
    assert "gmail-agent:" not in override


def test_container_entrypoint_seeds_the_reflex_cache_without_host_dependencies() -> None:
    entrypoint = (_ROOT / "scripts" / "container-entrypoint.sh").read_text(encoding="utf-8")
    dockerignore = (_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "Preparing the Mission Control frontend cache" in entrypoint
    assert "GMAIL_AGENT_REFLEX_WEB_DIR" in entrypoint
    assert "REFLEX_WEB_WORKDIR" in entrypoint
    assert 'cd "$runtime_app"' in entrypoint
    assert 'exec "$@"' in entrypoint
    assert "!assets/**" in dockerignore
    assert "!gmail_admin_agent/**" in dockerignore
    assert "!reflex.lock/**" in dockerignore


def test_container_workflow_validates_pull_requests_and_publishes_main() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert 'tags: ["v*.*.*"]' in workflow
    assert "packages: write" in workflow
    assert "ghcr.io/${{ github.repository }}" in workflow
    assert "type=raw,value=latest,enable={{is_default_branch}}" in workflow
    assert "type=semver,pattern={{version}}" in workflow
    assert "type=semver,pattern={{major}}.{{minor}}" in workflow
    assert (
        "type=semver,pattern={{major}},"
        "enable=${{ !startsWith(github.ref, 'refs/tags/v0.') }}" in workflow
    )
    assert "type=sha" not in workflow
    assert "push: ${{ github.event_name == 'push' }}" in workflow
    assert "Smoke-test pull request image" in workflow
