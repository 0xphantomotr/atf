import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class AIProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIProvider:
    id: str
    label: str
    base_url: str
    default_model: str
    curated_models: tuple[str, ...]


AI_PROVIDERS: dict[str, AIProvider] = {
    "openai": AIProvider(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
        curated_models=("gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1"),
    ),
    "gemini": AIProvider(
        id="gemini",
        label="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        curated_models=("gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"),
    ),
    "groq": AIProvider(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-20b",
        curated_models=(
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ),
    ),
}


def get_provider(provider_id: str) -> AIProvider:
    provider = AI_PROVIDERS.get(provider_id.strip().lower())
    if provider is None:
        raise AIProviderError("Provider i AI nuk mbështetet.")
    return provider


def list_provider_models(provider: AIProvider, api_key: str) -> list[str]:
    api_request = request.Request(
        f"{provider.base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with request.urlopen(api_request, timeout=30) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise AIProviderError(
            f"Nuk u verifikua API key për {provider.label}: {exc.code} {details[:300]}"
        ) from exc
    except error.URLError as exc:
        raise AIProviderError(
            f"Nuk u lidhëm dot me {provider.label}: {exc.reason}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise AIProviderError(f"{provider.label} ktheu përgjigje të pavlefshme.") from exc

    models = _extract_model_ids(payload)
    if not models:
        raise AIProviderError(f"{provider.label} nuk ktheu lista modelesh.")
    return models


def curated_models(provider: AIProvider) -> list[str]:
    return list(provider.curated_models)


def _extract_model_ids(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    model_ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if isinstance(model_id, str) and model_id:
            if model_id.startswith("models/"):
                model_id = model_id.removeprefix("models/")
            model_ids.append(model_id)
    return sorted(set(model_ids))
