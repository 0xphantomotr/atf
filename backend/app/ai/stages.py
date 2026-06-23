from typing import Any

AI_STAGES = ("extraction", "synthesis", "drafting", "correction")
AI_STAGE_LABELS = {
    "extraction": "Nxjerrja e dokumenteve",
    "synthesis": "Analiza specialistike",
    "drafting": "Hartimi i Aktit",
    "correction": "Korrigjimi i Aktit",
}


def normalize_ai_stage(stage: str) -> str:
    normalized = stage.strip().lower()
    if normalized not in AI_STAGES:
        allowed = ", ".join(AI_STAGES)
        raise ValueError(f"Faza AI duhet të jetë një nga: {allowed}.")
    return normalized


def normalize_stage_models(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for raw_stage, raw_model in value.items():
        if not isinstance(raw_stage, str) or not isinstance(raw_model, str):
            continue
        stage = raw_stage.strip().lower()
        model = raw_model.strip()
        if stage in AI_STAGES and model:
            normalized[stage] = model
    return normalized


def ai_settings_for_stage(
    ai_settings: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    normalized_stage = normalize_ai_stage(stage)
    resolved = dict(ai_settings)
    stage_models = normalize_stage_models(ai_settings.get("stage_models"))
    resolved["stage"] = normalized_stage
    resolved["model"] = stage_models.get(normalized_stage, str(ai_settings.get("model") or ""))
    return resolved


def resolved_stage_models(ai_settings: dict[str, Any]) -> dict[str, str]:
    return {
        stage: str(ai_settings_for_stage(ai_settings, stage).get("model") or "")
        for stage in AI_STAGES
    }
