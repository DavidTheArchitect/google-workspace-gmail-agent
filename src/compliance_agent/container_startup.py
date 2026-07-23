"""Container-only model initialization before the operator console starts."""

import asyncio
import sys

from compliance_agent.llm.readiness import (
    list_local_models,
    normalize_model_tag,
    pull_local_model,
    require_local_model,
)
from compliance_agent.settings import Settings, load_settings


async def ensure_configured_model(settings: Settings) -> str:
    """Install the configured Ollama model when absent and prove it is usable."""

    model = normalize_model_tag(settings.ollama_model, "model")
    installed = await list_local_models(settings)
    if model in installed:
        sys.stdout.write(f"[gmail-agent] Model {model} is already available.\n")
    else:
        sys.stdout.write(f"[gmail-agent] Pulling model {model}.\n")
        sys.stdout.flush()
        await pull_local_model(settings, model)
    await require_local_model(settings, model, require_vision=False)
    return model


def main() -> None:
    """Run the fail-closed container model bootstrap."""

    try:
        asyncio.run(ensure_configured_model(load_settings()))
    except Exception as error:
        sys.stderr.write(f"[gmail-agent] Model initialization failed: {error}\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
