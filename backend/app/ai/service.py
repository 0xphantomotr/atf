import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import UserAISetting
from app.ai.providers import (
    AIProviderError,
    AI_PROVIDERS,
    curated_models,
    get_provider,
    list_provider_models,
)
from app.ai.schemas import AIProviderRead, AISettingUpsert
from app.ai.stages import normalize_ai_stage, normalize_stage_models
from app.core.security import decrypt_secret, encrypt_secret, secret_hint


def list_providers() -> list[AIProviderRead]:
    return [
        AIProviderRead(
            id=provider.id,
            label=provider.label,
            default_model=provider.default_model,
            curated_models=curated_models(provider),
        )
        for provider in AI_PROVIDERS.values()
    ]


async def get_user_ai_setting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> UserAISetting | None:
    result = await session.execute(
        select(UserAISetting).where(
            UserAISetting.user_id == user_id,
            UserAISetting.is_enabled.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def require_user_ai_setting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> UserAISetting:
    setting = await get_user_ai_setting(session, user_id=user_id)
    if setting is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ky veprim kërkon API key AI. Konfiguroni provider-in dhe modelin "
                "përpara gjenerimit."
            ),
        )
    return setting


async def upsert_user_ai_setting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    payload: AISettingUpsert,
) -> tuple[UserAISetting, list[str]]:
    provider = get_provider(payload.provider)
    models = _validated_models(provider.id, payload.api_key)
    selected_model = _default_model_for_provider(provider.id, models)

    setting = await get_user_ai_setting(session, user_id=user_id)
    if setting is None:
        setting = UserAISetting(
            user_id=user_id,
            provider=provider.id,
            selected_model=selected_model,
            stage_models={},
            encrypted_api_key=encrypt_secret(payload.api_key),
            api_key_hint=secret_hint(payload.api_key),
            is_enabled=True,
        )
        session.add(setting)
    else:
        setting.provider = provider.id
        setting.selected_model = selected_model
        setting.stage_models = {}
        setting.encrypted_api_key = encrypt_secret(payload.api_key)
        setting.api_key_hint = secret_hint(payload.api_key)
        setting.is_enabled = True

    await session.commit()
    await session.refresh(setting)
    return setting, models


async def list_user_available_models(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> tuple[str, list[str], str]:
    setting = await require_user_ai_setting(session, user_id=user_id)
    provider = get_provider(setting.provider)
    api_key = decrypt_secret(setting.encrypted_api_key)
    try:
        return provider.id, list_provider_models(provider, api_key), "provider"
    except AIProviderError:
        return provider.id, curated_models(provider), "curated_fallback"


async def update_user_ai_model(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    model: str,
) -> UserAISetting:
    setting = await require_user_ai_setting(session, user_id=user_id)
    provider_id, models, _ = await list_user_available_models(session, user_id=user_id)
    if model not in models:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Modeli nuk u gjet te {provider_id}. Përdorni /ai_models për listën.",
        )

    setting.selected_model = model
    await session.commit()
    await session.refresh(setting)
    return setting


async def update_user_ai_stage_model(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    stage: str,
    model: str,
) -> UserAISetting:
    setting = await require_user_ai_setting(session, user_id=user_id)
    normalized_stage = _validated_stage(stage)
    provider_id, models, _ = await list_user_available_models(session, user_id=user_id)
    if model not in models:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Modeli nuk u gjet te {provider_id}. Përdorni /ai_models për listën.",
        )

    stage_models = normalize_stage_models(setting.stage_models)
    stage_models[normalized_stage] = model
    setting.stage_models = stage_models
    await session.commit()
    await session.refresh(setting)
    return setting


async def clear_user_ai_stage_model(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    stage: str,
) -> UserAISetting:
    setting = await require_user_ai_setting(session, user_id=user_id)
    normalized_stage = _validated_stage(stage)
    stage_models = normalize_stage_models(setting.stage_models)
    stage_models.pop(normalized_stage, None)
    setting.stage_models = stage_models
    await session.commit()
    await session.refresh(setting)
    return setting


async def delete_user_ai_setting(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> None:
    await session.execute(delete(UserAISetting).where(UserAISetting.user_id == user_id))
    await session.commit()


async def get_user_ai_credentials(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> dict[str, Any] | None:
    setting = await get_user_ai_setting(session, user_id=user_id)
    if setting is None:
        return None

    provider = get_provider(setting.provider)
    return {
        "provider": provider.id,
        "provider_label": provider.label,
        "base_url": provider.base_url,
        "model": setting.selected_model,
        "stage_models": normalize_stage_models(setting.stage_models),
        "api_key": decrypt_secret(setting.encrypted_api_key),
        "api_key_hint": setting.api_key_hint,
    }


def _validated_models(provider_id: str, api_key: str) -> list[str]:
    provider = get_provider(provider_id)
    try:
        return list_provider_models(provider, api_key)
    except AIProviderError as exc:
        if exc.allow_curated_fallback:
            return curated_models(provider)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _default_model_for_provider(provider_id: str, models: list[str]) -> str:
    provider = get_provider(provider_id)
    if provider.default_model in models:
        return provider.default_model
    for curated_model in curated_models(provider):
        if curated_model in models:
            return curated_model
    return models[0] if models else provider.default_model


def _validated_stage(stage: str) -> str:
    try:
        return normalize_ai_stage(stage)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
