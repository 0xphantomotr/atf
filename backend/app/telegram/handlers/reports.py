from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from app.db.session import AsyncSessionLocal
from app.reviews import service as reviews_service
from app.telegram.service import (
    get_active_project,
    get_latest_completed_review_job,
    get_latest_review_job,
    get_or_create_message_user,
)

router = Router()


@router.message(Command("raportet", "reports"))
async def reports_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Nuk keni projekt aktiv.")
            return

        completed_job = await get_latest_completed_review_job(
            session,
            user_id=user.id,
            project_id=project.id,
        )
        if completed_job is None:
            latest_job = await get_latest_review_job(
                session,
                user_id=user.id,
                project_id=project.id,
            )
            if latest_job is not None:
                await message.answer(
                    "Draft Akt Kolaudimi nuk është ende gati.\n\n"
                    f"Statusi aktual: {latest_job.status} ({latest_job.progress}%)."
                )
            else:
                await message.answer(
                    "Nuk ka ende Akt Kolaudimi për projektin aktiv.\n\n"
                    "Niseni gjenerimin me /gjenero."
                )
            return

        _, outputs = await reviews_service.get_review_job_outputs(
            session,
            job_id=completed_job.id,
            user_id=user.id,
        )
        pdf_output = next(
            (output for output in outputs if output.output_type == "pdf"),
            None,
        )
        if pdf_output is None:
            await message.answer("Nuk u gjet PDF për Akt Kolaudimin e fundit.")
            return

        download = await reviews_service.download_generated_output(
            session,
            output_id=pdf_output.id,
            user_id=user.id,
        )

    await message.answer_document(
        BufferedInputFile(download.content, filename=download.filename),
        caption=f"Draft Akt Kolaudimi për projektin: {project.name}",
    )
