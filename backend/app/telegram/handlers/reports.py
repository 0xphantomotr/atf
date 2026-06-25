from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from app.db.session import AsyncSessionLocal
from app.reviews import service as reviews_service
from app.telegram.service import (
    display_review_job_error,
    display_retry_time,
    get_active_project,
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

        latest_job = await get_latest_review_job(
            session,
            user_id=user.id,
            project_id=project.id,
        )
        if latest_job is None:
            await message.answer(
                "Nuk ka ende Akt Kolaudimi për projektin aktiv.\n\n"
                "Niseni gjenerimin me /gjenero."
            )
            return

        if latest_job.status == "failed":
            await message.answer(
                "Gjenerimi i fundit dështoi dhe nuk prodhoi Akt Kolaudimi.\n\n"
                f"Gabim: {display_review_job_error(latest_job.error_message)}\n\n"
                "Rregulloni konfigurimin dhe përdorni përsëri /gjenero."
            )
            return

        if latest_job.status == "waiting_for_quota":
            await message.answer(
                "Akt Kolaudimi nuk është ende gati.\n\n"
                "Modeli AI arriti limitin e përkohshëm të kuotës dhe gjenerimi "
                "do të vazhdojë automatikisht nga hapi i fundit i ruajtur.\n\n"
                f"Riprovohet: {display_retry_time(latest_job.retry_after_at)}\n"
                "Përdorni /status për ecurinë."
            )
            return

        if latest_job.status != "completed":
            await message.answer(
                "Akt Kolaudimi nuk është ende gati.\n\n"
                f"Statusi aktual: {latest_job.status} ({latest_job.progress}%)."
            )
            return

        _, outputs = await reviews_service.get_review_job_outputs(
            session,
            job_id=latest_job.id,
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
        caption=f"Akt Kolaudimi për projektin: {project.name}",
    )
