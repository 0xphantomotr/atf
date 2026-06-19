from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.db.session import AsyncSessionLocal
from app.projects import service as projects_service
from app.projects.schemas import ProjectCreate
from app.telegram.service import get_or_create_message_user

router = Router()


@router.message(Command("projekt_ri", "newproject"))
async def create_project(message: Message, command: CommandObject) -> None:
    project_name = (command.args or "").strip()
    if not project_name:
        await message.answer(
            "Shkruani emrin e projektit pas komandës.\n\n"
            "Shembull:\n"
            "/projekt_ri Godinë banimi 5 kate"
        )
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await projects_service.create_project(
            session,
            payload=ProjectCreate(
                name=project_name,
                project_type="residential",
                stage="during_construction",
                location=None,
                description=None,
            ),
            user_id=user.id,
        )

    await message.answer(
        "Projekti u krijua.\n\n"
        f"Emri: {project.name}\n"
        "Tipi: residential\n"
        "Faza: during_construction\n\n"
        "Tani mund të dërgoni PDF, DOCX ose ZIP në këtë bisedë."
    )


@router.message(Command("projektet", "projects"))
async def list_projects(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        projects = await projects_service.list_projects(session, user_id=user.id)

    if not projects:
        await message.answer(
            "Nuk keni ende projekte.\n\n"
            "Krijoni një projekt me:\n"
            "/projekt_ri Emri i projektit"
        )
        return

    lines = ["Projektet tuaja:", ""]
    for index, project in enumerate(projects, start=1):
        active = " (aktiv)" if index == 1 else ""
        lines.append(f"{index}. {project.name}{active}")
        lines.append(f"   {project.project_type}, {project.stage}")
    lines.append("")
    lines.append("Projekti aktiv është projekti më i fundit në listë.")
    await message.answer("\n".join(lines))


@router.callback_query(F.data == "project:create")
async def create_project_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(
            "Krijoni projektin me këtë format:\n\n"
            "/projekt_ri Godinë banimi 5 kate"
        )
    await callback.answer()


@router.callback_query(F.data == "project:list")
async def list_projects_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await list_projects(callback.message)
    await callback.answer()
