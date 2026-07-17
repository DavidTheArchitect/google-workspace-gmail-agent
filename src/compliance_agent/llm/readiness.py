"""Fail-closed readiness checks for locally hosted Ollama models."""

import re
from urllib.parse import urlsplit, urlunsplit

import httpx2

from compliance_agent.settings import Settings

_MODEL_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MODEL_PULL_TIMEOUT_SECONDS = 1_800


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


async def list_local_models(settings: Settings) -> tuple[str, ...]:
    """Return the validated model tags currently installed in local Ollama."""

    endpoint = _ollama_native_endpoint(str(settings.ollama_base_url), "/api/tags")
    try:
        async with httpx2.AsyncClient(timeout=settings.llm_request_timeout_seconds) as client:
            response = await client.get(endpoint)
            response.raise_for_status()
            payload = response.json()
    except Exception as error:
        message = "installed Ollama models could not be read"
        raise RuntimeError(message) from error

    raw_models = payload.get("models", []) if isinstance(payload, dict) else []
    if not isinstance(raw_models, list):
        raw_models = []
    names: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        candidate = item.get("model") or item.get("name")
        if isinstance(candidate, str) and _MODEL_TAG.fullmatch(candidate):
            names.add(candidate)
    return tuple(sorted(names, key=str.casefold))


async def pull_local_model(settings: Settings, model_tag: str) -> str:
    """Download one validated model through the loopback Ollama API."""

    normalized = normalize_model_tag(model_tag, "model")
    endpoint = _ollama_native_endpoint(str(settings.ollama_base_url), "/api/pull")
    try:
        async with httpx2.AsyncClient(timeout=_MODEL_PULL_TIMEOUT_SECONDS) as client:
            response = await client.post(
                endpoint,
                json={"model": normalized, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as error:
        message = f"local Ollama model {normalized!r} could not be added"
        raise RuntimeError(message) from error
    if not isinstance(payload, dict) or payload.get("status") != "success":
        message = f"local Ollama model {normalized!r} could not be added"
        raise RuntimeError(message)
    return normalized


def normalize_model_tag(value: str, label: str) -> str:
    """Validate one registry-safe local Ollama model tag."""

    normalized = value.strip()
    if _MODEL_TAG.fullmatch(normalized) is None:
        message = f"{label} must be a valid local Ollama model tag"
        raise ValueError(message)
    return normalized


def _ollama_native_endpoint(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
