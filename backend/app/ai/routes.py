from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service
from app.ai.schemas import (
    AIModelUpdate,
    AIModelsRead,
    AIProviderRead,
    AISettingRead,
    AISettingUpsert,
)
from app.db.session import get_session
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/users/me/ai-settings", tags=["ai-settings"])


@router.get("/providers", response_model=list[AIProviderRead])
async def providers() -> list[AIProviderRead]:
    return service.list_providers()


@router.get("", response_model=AISettingRead | None)
async def get_ai_setting(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AISettingRead | None:
    setting = await service.get_user_ai_setting(session, user_id=current_user.id)
    return AISettingRead.model_validate(setting) if setting else None


@router.put("", response_model=AISettingRead)
async def save_ai_setting(
    payload: AISettingUpsert,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AISettingRead:
    setting, _ = await service.upsert_user_ai_setting(
        session,
        user_id=current_user.id,
        payload=payload,
    )
    return AISettingRead.model_validate(setting)


@router.get("/models", response_model=AIModelsRead)
async def list_ai_models(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AIModelsRead:
    provider, models, source = await service.list_user_available_models(
        session,
        user_id=current_user.id,
    )
    return AIModelsRead(provider=provider, models=models, source=source)


@router.patch("/model", response_model=AISettingRead)
async def update_ai_model(
    payload: AIModelUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AISettingRead:
    setting = await service.update_user_ai_model(
        session,
        user_id=current_user.id,
        model=payload.model,
    )
    return AISettingRead.model_validate(setting)


@router.patch("/model/{stage}", response_model=AISettingRead)
async def update_ai_stage_model(
    stage: str,
    payload: AIModelUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AISettingRead:
    setting = await service.update_user_ai_stage_model(
        session,
        user_id=current_user.id,
        stage=stage,
        model=payload.model,
    )
    return AISettingRead.model_validate(setting)


@router.delete("/model/{stage}", response_model=AISettingRead)
async def clear_ai_stage_model(
    stage: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AISettingRead:
    setting = await service.clear_user_ai_stage_model(
        session,
        user_id=current_user.id,
        stage=stage,
    )
    return AISettingRead.model_validate(setting)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_setting(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await service.delete_user_ai_setting(session, user_id=current_user.id)
