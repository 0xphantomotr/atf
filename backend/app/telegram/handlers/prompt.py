import asyncio
from uuid import UUID

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
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
from app.prompting.executor import PromptExecutionError, execute_prompt_plan
from app.prompting.planner import PromptPlanningError, plan_prompt
from app.prompting.policy import PromptPolicyError, validate_prompt_plan
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
)

router = Router()
logger = get_logger(__name__)

PROMPT_HELP = (
    "Shkruani një kërkesë pas komandës /prompt.\n\n"
    "Mund të listoni, krijoni ose zgjidhni projekte, të kontrolloni statusin "
    "dhe të importoni një dokument/ZIP të bashkëngjitur.\n\n"
    "Shembuj:\n"
    "/prompt Shfaq projektet e mia\n"
    "/prompt Krijo projektin Test Dosja Teknike\n"
    "/prompt Zgjidh projektin Test Dosja Teknike\n"
    "/prompt Cili është projekti aktiv?\n"
    "/prompt Shfaq statusin\n\n"
    "Me attachment:\n"
    "/prompt Krijo projektin Test, zgjidhe dhe importo dosjen e bashkëngjitur\n\n"
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
            if planner_result.plan.needs_clarification:
                await message.answer(
                    planner_result.plan.clarification_question
                    or "Ju lutem sqaroni kërkesën."
                )
                return

            if has_attachment:
                from app.workers.jobs import run_prompt_workflow

                run_prompt_workflow.send(str(run.id))
                await message.answer(
                    "Kërkesa u vendos në radhë.\n\n"
                    "Attachment-i u ruajt dhe do të importohet në projekt. "
                    "Do t'ju njoftoj kur përpunimi i dokumenteve të përfundojë."
                )
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
