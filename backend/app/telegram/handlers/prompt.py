import asyncio
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import AIQuotaLimitError, LLMReviewError
from app.ai import service as ai_service
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.files.service import MAX_UPLOAD_BYTES
from app.projects import service as projects_service
from app.prompting import service as prompting_service
from app.prompting.attachments import persist_prompt_attachment
from app.prompting.confirmation import (
    PromptConfirmationError,
    cancel_generation,
    cancel_latest_waiting_confirmation,
    confirm_generation,
)
from app.prompting.context import (
    cancel_pending_clarification,
    clarification_message,
    load_pending_clarification,
    load_recent_prompt_turns,
    resolve_pending_clarification,
)
from app.prompting.executor import PromptExecutionError, execute_prompt_plan
from app.prompting.generation import plan_requires_background
from app.prompting.planner import PromptPlanningError, plan_prompt
from app.prompting.policy import PromptPolicyError, validate_prompt_plan
from app.prompting.preview import format_plan_preview, is_quiet_question_plan
from app.prompting.quota import PromptQuotaError, enforce_prompt_quota
from app.prompting.schemas import (
    PromptPlanningContext,
    PromptProjectContext,
)
from app.prompting.security import (
    SECRET_REPLACEMENT,
    contains_likely_secret,
)
from app.telegram.service import (
    build_upload_from_message,
    get_active_project,
    get_or_create_message_user,
    get_or_create_telegram_user,
)

router = Router()
logger = get_logger(__name__)

PROMPT_HELP = (
    "Shkruani një kërkesë pas komandës /prompt.\n\n"
    "Mund të listoni, krijoni ose zgjidhni projekte, të kontrolloni statusin "
    "dhe të importoni një dokument/ZIP të bashkëngjitur. Gjithashtu mund të "
    "vlerësoni, konfirmoni, gjeneroni dhe merrni Akt-Kolaudimin PDF.\n\n"
    "Shembuj:\n"
    "/prompt Shfaq projektet e mia\n"
    "/prompt Krijo projektin Test Dosja Teknike\n"
    "/prompt Zgjidh projektin Test Dosja Teknike\n"
    "/prompt Cili është projekti aktiv?\n"
    "/prompt Shfaq statusin\n"
    "/prompt Përdor modelin gemini-3.1-flash-lite\n"
    "/prompt Kush është sipërmarrësi sipas dosjes aktive?\n\n"
    "/prompt Importo dosjen nga LINKU_DRIVE, gjenero Akt-Kolaudimin dhe ruaje "
    "në të njëjtin folder\n\n"
    "Me attachment:\n"
    "/prompt Krijo projektin Test, importo dosjen, gjenero Akt-Kolaudimin "
    "dhe ma dërgo PDF-në\n\n"
    "Për pyetje pasuese ose sqarime, përdorni përsëri /prompt.\n"
    "API key vendoset vetëm me /ai_key dhe jo brenda /prompt."
)


@router.message(Command("prompt"))
async def prompt_command(
    message: Message,
    command: CommandObject,
    telegram_update_id: int | None = None,
) -> None:
    prompt_text = (command.args or "").strip()
    if not prompt_text:
        await message.answer(PROMPT_HELP)
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        safe_prompt = (
            SECRET_REPLACEMENT if contains_likely_secret(prompt_text) else prompt_text
        )
        run, created = await prompting_service.create_or_get_prompt_run(
            session,
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            telegram_message_id=message.message_id,
            telegram_update_id=telegram_update_id,
            original_prompt=safe_prompt,
        )
        if not created:
            return

        try:
            await enforce_prompt_quota(session, user_id=user.id)
        except PromptQuotaError as exc:
            await prompting_service.fail_prompt_run(
                session,
                run_id=run.id,
                code=exc.code,
                detail=exc.user_message,
            )
            await message.answer(exc.user_message)
            return

        if contains_likely_secret(prompt_text):
            detail = (
                "Për siguri, /prompt nuk pranon API key, token ose sekrete. "
                "Përdorni /ai_key provider api_key dhe fshini mesazhin me sekret."
            )
            await prompting_service.fail_prompt_run(
                session,
                run_id=run.id,
                code="secret_in_prompt",
                detail=detail,
            )
            await message.answer(detail)
            return

        try:
            ai_settings = await ai_service.get_user_ai_credentials(
                session,
                user_id=user.id,
            )
        except Exception as exc:
            logger.exception(
                "prompt_ai_settings_failed",
                prompt_run_id=str(run.id),
                error_type=type(exc).__name__,
            )
            detail = (
                "Konfigurimi AI nuk mund të lexohet. Ruajeni përsëri API key me /ai_key."
            )
            await prompting_service.fail_prompt_run(
                session,
                run_id=run.id,
                code="ai_settings_unreadable",
                detail=detail,
            )
            await message.answer(detail)
            return
        if ai_settings is None:
            detail = (
                "Përdorimi i /prompt kërkon konfigurim AI.\n\n"
                "Ruani API key me /ai_key provider api_key, pastaj zgjidhni modelin "
                "me /ai_model."
            )
            await prompting_service.fail_prompt_run(
                session,
                run_id=run.id,
                code="ai_settings_missing",
                detail=detail,
            )
            await message.answer(detail)
            return

        has_attachment = message.document is not None
        if has_attachment:
            try:
                document = message.document
                if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Dokumenti është më i madh se kufiri i lejuar prej 50 MB.",
                    )
                upload = await build_upload_from_message(message)
                await persist_prompt_attachment(
                    session,
                    run=run,
                    upload=upload,
                    telegram_file_id=document.file_id,
                    telegram_file_unique_id=document.file_unique_id,
                )
            except HTTPException as exc:
                await _fail_and_answer(
                    session,
                    message=message,
                    run_id=run.id,
                    code="attachment_persistence_failed",
                    detail=str(exc.detail),
                )
                return
            except Exception as exc:
                logger.exception(
                    "prompt_attachment_persistence_failed",
                    prompt_run_id=str(run.id),
                    error_type=type(exc).__name__,
                )
                await _fail_and_answer(
                    session,
                    message=message,
                    run_id=run.id,
                    code="attachment_persistence_failed",
                    detail="Attachment-i nuk u ruajt. Provoni përsëri.",
                )
                return

        projects = await projects_service.list_projects(session, user_id=user.id)
        active_project = await get_active_project(session, user_id=user.id)
        pending_run, pending_clarification = await load_pending_clarification(
            session,
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            exclude_run_id=run.id,
        )
        recent_turns = await load_recent_prompt_turns(
            session,
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            project_id=active_project.id if active_project else None,
            exclude_run_id=run.id,
        )
        configured_models = [str(ai_settings.get("model") or "")]
        stage_models = ai_settings.get("stage_models")
        if isinstance(stage_models, dict):
            configured_models.extend(str(value) for value in stage_models.values())
        context = PromptPlanningContext(
            projects=[
                PromptProjectContext(
                    name=project.name,
                    is_active=bool(active_project and project.id == active_project.id),
                )
                for project in projects
            ],
            has_ai_settings=True,
            has_attachment=has_attachment,
            configured_models=[
                value for value in dict.fromkeys(configured_models) if value
            ][:8],
            recent_turns=recent_turns,
            pending_clarification=pending_clarification,
        )

        try:
            planner_result = await asyncio.to_thread(
                plan_prompt,
                prompt_text,
                context=context,
                ai_settings=ai_settings,
            )
            validate_prompt_plan(
                planner_result.plan,
                has_attachment=has_attachment,
            )
            await prompting_service.save_prompt_plan(
                session,
                run=run,
                plan=planner_result.plan,
                provider=str(ai_settings["provider"]),
                model=str(ai_settings["model"]),
                token_usage=planner_result.token_usage,
            )
            if pending_run is not None:
                await resolve_pending_clarification(
                    session,
                    pending_run=pending_run,
                    resolved_by_run_id=run.id,
                )
            if planner_result.plan.needs_clarification:
                await message.answer(
                    clarification_message(
                        planner_result.plan.clarification_question
                        or "Ju lutem sqaroni kërkesën.",
                        options=planner_result.plan.clarification_options,
                    )
                )
                return

            quiet_question = is_quiet_question_plan(planner_result.plan)
            if not quiet_question:
                await message.answer(format_plan_preview(planner_result.plan))

            if plan_requires_background(planner_result.plan):
                from app.workers.jobs import run_prompt_workflow

                run_prompt_workflow.send(str(run.id))
                if quiet_question:
                    return
                if has_attachment:
                    queued_message = (
                        "Kërkesa u vendos në radhë.\n\n"
                        "Attachment-i u ruajt dhe do të importohet në projekt. "
                        "Pas leximit do të merrni vlerësimin dhe konfirmimin e gjenerimit."
                    )
                else:
                    queued_message = (
                        "Kërkesa u vendos në radhë. "
                        "Do të merrni vlerësimin para konfirmimit të gjenerimit."
                    )
                await message.answer(queued_message)
                return

            results = await execute_prompt_plan(
                session,
                run=run,
                plan=planner_result.plan,
                user_id=user.id,
            )
            await prompting_service.complete_prompt_run(session, run=run)
        except PromptPolicyError as exc:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code=exc.code,
                detail=exc.user_message,
            )
            return
        except PromptExecutionError as exc:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code=exc.code,
                detail=exc.user_message,
            )
            return
        except PromptPlanningError as exc:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code="planning_failed",
                detail=str(exc),
            )
            return
        except AIQuotaLimitError as exc:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code="planner_quota_limited",
                detail=str(exc),
            )
            return
        except LLMReviewError:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code="planner_provider_failed",
                detail="Planifikimi me AI dështoi. Kontrolloni provider-in dhe provoni përsëri.",
            )
            return
        except HTTPException as exc:
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code="service_error",
                detail=str(exc.detail),
            )
            return
        except Exception as exc:
            logger.exception(
                "prompt_command_failed",
                prompt_run_id=str(run.id),
                error_type=type(exc).__name__,
            )
            await _fail_and_answer(
                session,
                message=message,
                run_id=run.id,
                code="unexpected_error",
                detail="Kërkesa /prompt dështoi. Provoni përsëri ose përdorni komandat standarde.",
            )
            return

    await message.answer("\n\n".join(result.message for result in results))


@router.callback_query(F.data.startswith("pc:"))
async def prompt_confirmation_callback(callback: CallbackQuery) -> None:
    if callback.data is None or callback.message is None:
        await callback.answer("Konfirmimi nuk është i vlefshëm.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        user = await get_or_create_telegram_user(
            session,
            telegram_user=callback.from_user,
        )
        try:
            if callback.data.startswith("pc:c:"):
                result = await confirm_generation(
                    session,
                    callback_data=callback.data,
                    user_id=user.id,
                    telegram_chat_id=callback.message.chat.id,
                )
            else:
                result = await cancel_generation(
                    session,
                    callback_data=callback.data,
                    user_id=user.id,
                    telegram_chat_id=callback.message.chat.id,
                )
        except PromptConfirmationError as exc:
            if exc.terminal:
                try:
                    from app.prompting.confirmation import parse_confirmation_callback

                    _, run_id, _ = parse_confirmation_callback(callback.data)
                    await prompting_service.fail_prompt_run(
                        session,
                        run_id=run_id,
                        code=exc.code,
                        detail=exc.user_message,
                    )
                except PromptConfirmationError:
                    pass
            await callback.answer("Konfirmimi dështoi.", show_alert=True)
            await callback.message.answer(exc.user_message)
            return

    await callback.answer(result.message[:180])
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        logger.info(
            "prompt_confirmation_markup_not_removed",
            prompt_run_id=str(result.run_id),
        )
    await callback.message.answer(result.message)
    if result.status == "confirmed":
        from app.workers.jobs import run_prompt_workflow

        run_prompt_workflow.send(str(result.run_id))


@router.message(Command("anulo", "cancel"))
async def cancel_prompt_generation_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        result = await cancel_latest_waiting_confirmation(
            session,
            user_id=user.id,
            telegram_chat_id=message.chat.id,
        )
        clarification_cancelled = False
        if result is None:
            clarification_cancelled = await cancel_pending_clarification(
                session,
                user_id=user.id,
                telegram_chat_id=message.chat.id,
            )
    if result is None:
        if clarification_cancelled:
            await message.answer("Sqarimi në pritje u anulua.")
            return
        await message.answer(
            "Nuk ka gjenerim ose sqarim /prompt në pritje për anulim."
        )
        return
    await message.answer(result.message)


async def _fail_and_answer(
    session: AsyncSession,
    *,
    message: Message,
    run_id: UUID,
    code: str,
    detail: str,
) -> None:
    await prompting_service.fail_prompt_run(
        session,
        run_id=run_id,
        code=code,
        detail=detail,
    )
    await message.answer(detail)
