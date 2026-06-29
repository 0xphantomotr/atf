from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import AsyncSessionLocal
from app.prompting import service as prompting_service
from app.prompting.status import (
    generation_prompt_controls_latest_job,
    prompt_generation_status_message,
)
from app.reviews import service as reviews_service
from app.telegram.service import (
    display_review_job_error,
    display_review_job_stage,
    display_retry_time,
    document_progress_line,
    get_active_project,
    get_latest_review_job,
    get_or_create_message_user,
)

router = Router()


@router.message(Command("status"))
async def status_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Nuk keni projekt aktiv.")
            return

        job = await get_latest_review_job(
            session,
            user_id=user.id,
            project_id=project.id,
        )
        prompt_run = await prompting_service.get_latest_prompt_run(
            session,
            user_id=user.id,
            project_id=project.id,
            telegram_chat_id=message.chat.id,
        )
        if generation_prompt_controls_latest_job(prompt_run, job):
            if prompt_run.review_job_id is None:
                await message.answer(prompt_generation_status_message(prompt_run))
                return
            job = await reviews_service.get_review_job(
                session,
                job_id=prompt_run.review_job_id,
                user_id=user.id,
            )
        if job is None:
            await message.answer(
                "Nuk ka ende Akt Kolaudimi për projektin aktiv.\n\n"
                "Niseni me /gjenero."
            )
            return

        findings_count = 0
        if job.status == "completed":
            findings = await reviews_service.list_review_findings(
                session,
                job_id=job.id,
                user_id=user.id,
            )
            findings_count = len(findings)

    lines = [
        f"Projekti: {project.name}",
        f"Statusi: {job.status}",
        f"Progresi: {job.progress}%",
    ]
    stage_label = display_review_job_stage(job.current_stage)
    if stage_label:
        lines.append(f"Faza: {stage_label}")
    documents_line = document_progress_line(job.progress_details)
    if documents_line:
        lines.append(documents_line)

    if job.status == "completed":
        lines.append(f"Çështje për verifikim: {findings_count}")
        lines.append("Draft Akt Kolaudimin mund ta merrni me /raportet.")
    elif job.status == "waiting_for_quota":
        lines.extend(
            [
                "Modeli AI arriti limitin e përkohshëm të kuotës.",
                f"Riprovohet automatikisht: {display_retry_time(job.retry_after_at)}",
            ]
        )
        if job.retry_reason:
            lines.append(f"Arsye: {job.retry_reason}")
    elif job.status == "failed":
        lines.append(f"Gabim: {display_review_job_error(job.error_message)}")
    else:
        lines.append("Kontrolloni përsëri pas pak.")

    await message.answer("\n".join(lines))
