"""Fail-closed readiness checks for locally hosted Ollama models."""

from urllib.parse import urlsplit, urlunsplit

import httpx2

from compliance_agent.settings import Settings


async def require_local_model(
    settings: Settings,
    model_tag: str,
    *,
    require_vision: bool,
) -> None:
    """Prove the configured model exists and, when needed, advertises vision support."""

    endpoint = _ollama_native_endpoint(str(settings.ollama_base_url), "/api/show")
    try:
        async with httpx2.AsyncClient(timeout=settings.llm_request_timeout_seconds) as client:
            response = await client.post(endpoint, json={"model": model_tag})
            response.raise_for_status()
            payload = response.json()
    except Exception as error:
        message = f"local Ollama model {model_tag!r} is unavailable"
        raise RuntimeError(message) from error
    capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
    if require_vision and "vision" not in capabilities:
        message = f"browser model {model_tag!r} does not advertise Ollama vision capability"
        raise RuntimeError(message)


def _ollama_native_endpoint(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
