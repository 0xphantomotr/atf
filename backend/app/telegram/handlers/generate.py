from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import AsyncSessionLocal
from app.reviews import service as reviews_service
from app.reviews.schemas import GenerateRequest
from app.telegram.service import get_active_project, get_or_create_message_user

router = Router()


@router.message(Command("gjenero", "generate"))
async def generate_report(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer(
                "Nuk keni projekt aktiv.\n\n"
                "Krijoni një projekt me:\n"
                "/projekt_ri Emri i projektit"
            )
            return

        job = await reviews_service.create_review_job(
            session,
            project_id=project.id,
            user_id=user.id,
            payload=GenerateRequest(
                job_type="documentation_checklist",
                output_format="pdf",
                language="sq-AL",
                law_scope=["VKM_610_2022"],
            ),
        )

    await message.answer(
        "Auditimi u vendos në radhë.\n\n"
        f"Projekti: {project.name}\n"
        f"Statusi: {job.status}\n\n"
        "Përdorni /status për ecurinë dhe /raportet kur të përfundojë."
    )
