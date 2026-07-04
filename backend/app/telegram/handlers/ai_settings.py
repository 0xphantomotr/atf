from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from fastapi import HTTPException

from app.ai import service as ai_service
from app.ai.models import UserAISetting
from app.ai.schemas import AISettingUpsert
from app.ai.stages import AI_STAGE_LABELS, AI_STAGES, normalize_stage_models
from app.db.session import AsyncSessionLocal
from app.telegram.service import get_or_create_message_user, get_or_create_telegram_user

router = Router()


@router.message(Command("ai", "ai_status"))
async def ai_status(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        setting = await ai_service.get_user_ai_setting(session, user_id=user.id)

    if setting is None:
        await message.answer(_ai_help_text())
        return

    await message.answer(_ai_status_text(setting))


def _ai_status_text(setting: UserAISetting) -> str:
    return (
        "AI është konfiguruar.\n\n"
        f"Provider: {setting.provider}\n"
        f"Modeli bazë: {setting.selected_model}\n"
        f"API key: {setting.api_key_hint}\n\n"
        "Modelet sipas fazës:\n"
        + _stage_models_text(setting.selected_model, setting.stage_models)
        + "\n\nNdryshoni modelin bazë me /ai_model.\n"
        "Vendosni model faze me /ai_stage.\n"
        "Zëvendësoni key me /ai_key.\n"
        "Fshijeni me /ai_delete."
    )


@router.message(Command("ai_key"))
async def save_ai_key(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) != 2:
        await message.answer(
            "Formati:\n\n"
            "/ai_key provider api_key\n\n"
            "Provider të mbështetur: openai, gemini, groq\n"
            "Shembull:\n"
            "/ai_key groq gsk_..."
        )
        return

    provider, api_key = args
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        try:
            setting, models = await ai_service.upsert_user_ai_setting(
                session,
                user_id=user.id,
                payload=AISettingUpsert(provider=provider, api_key=api_key),
            )
        except HTTPException as exc:
            await message.answer(f"Nuk u ruajt API key.\n\n{exc.detail}")
            return

    await message.answer(
        "API key u verifikua dhe u ruajt.\n\n"
        f"Provider: {setting.provider}\n"
        f"Modeli aktual: {setting.selected_model}\n"
        f"API key: {setting.api_key_hint}\n\n"
        + _models_preview(models)
        + "\n\nPër siguri, fshini mesazhin ku dërguat API key nëse doni."
    )


@router.message(Command("ai_models"))
async def ai_models(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        try:
            provider, models, source = await ai_service.list_user_available_models(
                session,
                user_id=user.id,
            )
        except HTTPException as exc:
            await message.answer(f"Nuk u morën modelet.\n\n{exc.detail}")
            return

    await message.answer(
        f"Modelet për {provider} ({source}):\n\n"
        + _models_preview(models)
        + "\n\nZgjidhni modelin me:\n"
        "/ai_model emri_i_modelit"
    )


@router.message(Command("ai_model"))
async def update_ai_model(message: Message, command: CommandObject) -> None:
    model = (command.args or "").strip()
    if not model:
        await message.answer(
            "Shkruani modelin pas komandës.\n\n"
            "Shembull:\n"
            "/ai_model openai/gpt-oss-20b"
        )
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        try:
            setting = await ai_service.update_user_ai_model(
                session,
                user_id=user.id,
                model=model,
            )
        except HTTPException as exc:
            await message.answer(f"Nuk u ndryshua modeli.\n\n{exc.detail}")
            return

    await message.answer(
        "Modeli u përditësua.\n\n"
        f"Provider: {setting.provider}\n"
        f"Modeli: {setting.selected_model}"
    )


@router.message(Command("ai_stage"))
async def update_ai_stage_model(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip().split(maxsplit=1)
    if len(args) != 2:
        await message.answer(
            "Formati:\n\n"
            "/ai_stage faza modeli\n\n"
            "Fazat: extraction, synthesis, drafting, correction\n"
            "Përdorni 'default' si model për të hequr override-in."
        )
        return

    stage, model = args
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        try:
            if model.lower() == "default":
                setting = await ai_service.clear_user_ai_stage_model(
                    session,
                    user_id=user.id,
                    stage=stage,
                )
                action = "Override-i u hoq"
            else:
                setting = await ai_service.update_user_ai_stage_model(
                    session,
                    user_id=user.id,
                    stage=stage,
                    model=model,
                )
                action = "Modeli i fazës u përditësua"
        except HTTPException as exc:
            await message.answer(f"Nuk u ndryshua modeli i fazës.\n\n{exc.detail}")
            return

    await message.answer(
        f"{action}.\n\n"
        + _stage_models_text(setting.selected_model, setting.stage_models)
    )


@router.message(Command("ai_delete"))
async def delete_ai_key(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        await ai_service.delete_user_ai_setting(session, user_id=user.id)

    await message.answer("Konfigurimi AI dhe API key u fshinë.")


@router.callback_query(F.data == "ai:settings")
async def ai_settings_callback(callback: CallbackQuery) -> None:
    if callback.message:
        async with AsyncSessionLocal() as session:
            user = await get_or_create_telegram_user(
                session,
                telegram_user=callback.from_user,
            )
            setting = await ai_service.get_user_ai_setting(session, user_id=user.id)

        if setting is None:
            await callback.message.answer(_ai_help_text())
        else:
            await callback.message.answer(_ai_status_text(setting))
    await callback.answer()


def _ai_help_text() -> str:
    return (
        "AI nuk është konfiguruar ende.\n\n"
        "Gjenerimi i Draft Akt Kolaudimit kërkon API key personale.\n\n"
        "1. Ruani key:\n"
        "/ai_key provider api_key\n\n"
        "Provider të mbështetur: openai, gemini, groq\n\n"
        "2. Shfaqni modelet:\n"
        "/ai_models\n\n"
        "3. Zgjidhni model:\n"
        "/ai_model emri_i_modelit\n\n"
        "4. Opsionale, model sipas fazës:\n"
        "/ai_stage extraction emri_i_modelit\n\n"
        "Statusi: /ai\n"
        "Fshirja: /ai_delete"
    )


def _models_preview(models: list[str]) -> str:
    if not models:
        return "Nuk u gjetën modele."
    lines = []
    for model in models[:15]:
        lines.append(f"- {model}")
    if len(models) > 15:
        lines.append(f"... edhe {len(models) - 15} modele të tjera.")
    return "\n".join(lines)


def _stage_models_text(default_model: str, value: object) -> str:
    overrides = normalize_stage_models(value)
    lines = []
    for stage in AI_STAGES:
        model = overrides.get(stage, default_model)
        suffix = "" if stage in overrides else " (bazë)"
        lines.append(f"- {AI_STAGE_LABELS[stage]} [{stage}]: {model}{suffix}")
    return "\n".join(lines)
