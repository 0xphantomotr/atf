from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import AsyncSessionLocal
from app.reviews import service as reviews_service
from app.telegram.service import (
    display_review_job_error,
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
    if job.status == "completed":
        lines.append(f"Çështje për verifikim: {findings_count}")
        lines.append("Draft Akt Kolaudimin mund ta merrni me /raportet.")
    elif job.status == "failed":
        lines.append(f"Gabim: {display_review_job_error(job.error_message)}")
    else:
        lines.append("Kontrolloni përsëri pas pak.")

    await message.answer("\n".join(lines))
