from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import HTTPException

from app.db.session import AsyncSessionLocal
from app.projects import service as projects_service
from app.projects.models import Project
from app.projects.schemas import ProjectCreate
from app.telegram.service import (
    get_active_project,
    get_or_create_message_user,
    get_or_create_telegram_user,
    set_active_project,
)

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
        await set_active_project(session, user_id=user.id, project_id=project.id)

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
        active_project = await get_active_project(session, user_id=user.id)

    await _answer_project_list(
        message,
        projects=projects,
        active_project_id=active_project.id if active_project else None,
    )


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
        async with AsyncSessionLocal() as session:
            user = await get_or_create_telegram_user(
                session,
                telegram_user=callback.from_user,
            )
            projects = await projects_service.list_projects(session, user_id=user.id)
            active_project = await get_active_project(session, user_id=user.id)

        await _answer_project_list(
            callback.message,
            projects=projects,
            active_project_id=active_project.id if active_project else None,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("project:select:"))
async def select_project_callback(callback: CallbackQuery) -> None:
    project_id_text = (callback.data or "").removeprefix("project:select:")
    try:
        project_id = UUID(project_id_text)
    except ValueError:
        await callback.answer("Projekti nuk u njoh.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        user = await get_or_create_telegram_user(
            session,
            telegram_user=callback.from_user,
        )
        try:
            project = await set_active_project(
                session,
                user_id=user.id,
                project_id=project_id,
            )
        except HTTPException:
            await callback.answer("Projekti nuk u gjet.", show_alert=True)
            return
        projects = await projects_service.list_projects(session, user_id=user.id)

    if callback.message:
        await _answer_project_list(
            callback.message,
            projects=projects,
            active_project_id=project.id,
            intro=f"Projekti aktiv u zgjodh: {project.name}",
        )
    await callback.answer("Projekti aktiv u përditësua.")


async def _answer_project_list(
    message: Message,
    *,
    projects: list[Project],
    active_project_id: UUID | None,
    intro: str | None = None,
) -> None:
    if not projects:
        await message.answer(
            "Nuk keni ende projekte.\n\n"
            "Krijoni një projekt me:\n"
            "/projekt_ri Emri i projektit"
        )
        return

    lines = [intro or "Projektet tuaja:", ""]
    for index, project in enumerate(projects, start=1):
        active = " (aktiv)" if project.id == active_project_id else ""
        lines.append(f"{index}. {project.name}{active}")
        lines.append(f"   {project.project_type}, {project.stage}")
    lines.append("")
    lines.append("Zgjidhni projektin aktiv me butonat më poshtë.")
    await message.answer(
        "\n".join(lines),
        reply_markup=_projects_keyboard(projects, active_project_id=active_project_id),
    )


def _projects_keyboard(
    projects: list[Project],
    *,
    active_project_id: UUID | None,
) -> InlineKeyboardMarkup:
    rows = []
    for project in projects[:20]:
        prefix = "✓ " if project.id == active_project_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{prefix}{project.name}"[:64],
                    callback_data=f"project:select:{project.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
