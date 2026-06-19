from fastapi import HTTPException

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.db.session import AsyncSessionLocal
from app.files import service as files_service
from app.telegram.messages import UNSUPPORTED_FORMAT_MESSAGE
from app.telegram.service import (
    build_upload_from_message,
    get_active_project,
    get_or_create_message_user,
    store_telegram_document,
)

router = Router()


@router.message(Command("ngarko", "upload"))
async def upload_help(message: Message) -> None:
    await message.answer(
        "Dërgoni këtu një dokument PDF, DOCX, XLSX, JPG, PNG ose një arkiv ZIP.\n\n"
        "Dokumenti do të ruhet te projekti aktiv. Projekti aktiv është projekti "
        "më i fundit i krijuar."
    )


@router.message(Command("dokumentet", "documents"))
async def list_documents(message: Message) -> None:
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
        files = await files_service.list_project_files(
            session,
            project_id=project.id,
            user_id=user.id,
        )

    if not files:
        await message.answer("Projekti aktiv nuk ka ende dokumente të ngarkuara.")
        return

    lines = [f"Dokumentet për projektin: {project.name}", ""]
    for index, project_file in enumerate(files[:25], start=1):
        lines.append(f"{index}. {project_file.original_filename}")
    if len(files) > 25:
        lines.append(f"... edhe {len(files) - 25} dokumente të tjera.")
    await message.answer("\n".join(lines))


@router.message(F.document)
async def upload_document(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer(
                "Para ngarkimit duhet të krijoni një projekt.\n\n"
                "Shembull:\n"
                "/projekt_ri Godinë banimi 5 kate"
            )
            return

        try:
            upload = await build_upload_from_message(message)
            uploaded_count, skipped_count = await store_telegram_document(
                session,
                project_id=project.id,
                user_id=user.id,
                upload=upload,
            )
        except HTTPException:
            await message.answer(UNSUPPORTED_FORMAT_MESSAGE)
            return

    await message.answer(
        "Dokumenti u pranua për përpunim.\n\n"
        f"Projekti: {project.name}\n"
        f"Dokumente të importuara: {uploaded_count}\n"
        f"Të anashkaluara: {skipped_count}\n\n"
        "Përdorni /dokumentet për listën ose /gjenero për auditim pasi përpunimi të mbarojë."
    )
