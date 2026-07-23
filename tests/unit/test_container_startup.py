"""Tests for fail-closed container model initialization."""

import pytest

from compliance_agent import container_startup
from compliance_agent.settings import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("installed", [True, False])
async def test_configured_model_is_pulled_only_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    installed: bool,
) -> None:
    settings = Settings()
    pulled: list[str] = []
    required: list[tuple[str, bool]] = []

    async def list_models(_settings: Settings) -> tuple[str, ...]:
        return (settings.ollama_model,) if installed else ()

    async def pull_model(_settings: Settings, model: str) -> str:
        pulled.append(model)
        return model

    async def require_model(
        _settings: Settings,
        model: str,
        *,
        require_vision: bool,
    ) -> None:
        required.append((model, require_vision))

    monkeypatch.setattr(container_startup, "list_local_models", list_models)
    monkeypatch.setattr(container_startup, "pull_local_model", pull_model)
    monkeypatch.setattr(container_startup, "require_local_model", require_model)

    assert await container_startup.ensure_configured_model(settings) == settings.ollama_model
    assert pulled == ([] if installed else [settings.ollama_model])
    assert required == [(settings.ollama_model, False)]


@pytest.mark.asyncio
async def test_invalid_model_is_rejected_before_catalog_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_list(_settings: Settings) -> tuple[str, ...]:
        pytest.fail("catalog should not be queried for an invalid model tag")

    monkeypatch.setattr(container_startup, "list_local_models", unexpected_list)

    with pytest.raises(ValueError, match="valid local Ollama model tag"):
        await container_startup.ensure_configured_model(Settings(ollama_model="bad model"))


def test_container_startup_reports_failure_and_exits_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail(_settings: Settings) -> str:
        message = "model pull failed"
        raise RuntimeError(message)

    monkeypatch.setattr(container_startup, "load_settings", Settings)
    monkeypatch.setattr(container_startup, "ensure_configured_model", fail)

    with pytest.raises(SystemExit) as raised:
        container_startup.main()

    assert raised.value.code == 1
    assert "Model initialization failed: model pull failed" in capsys.readouterr().err
