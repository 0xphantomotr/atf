from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db.session import AsyncSessionLocal
from app.google_drive.oauth import (
    GoogleDriveAuthError,
    create_google_authorization_url,
    delete_google_drive_connection,
    google_drive_connection_status,
    google_drive_is_configured,
)
from app.google_drive.service import (
    GoogleDriveError,
    bind_google_drive_folder,
    google_drive_binding_status,
    preflight_google_drive_folder,
    unbind_google_drive_folder,
)
from app.prompting import service as prompting_service
from app.prompting.schemas import PromptAction, PromptActionArguments, PromptPlan
from app.telegram.service import (
    get_active_project,
    get_or_create_message_user,
)
from app.workers.jobs import run_prompt_workflow

router = Router()


@router.message(Command("google_connect", "drive_connect"))
async def google_connect_command(message: Message) -> None:
    if not google_drive_is_configured():
        await message.answer(
            "Google Drive nuk është konfiguruar ende në server. Kontaktoni administratorin."
        )
        return
    try:
        async with AsyncSessionLocal() as session:
            user = await get_or_create_message_user(session, message)
            authorization_url = await create_google_authorization_url(
                session,
                user_id=user.id,
            )
    except GoogleDriveAuthError as exc:
        await message.answer(str(exc))
        return

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Lidh Google Drive", url=authorization_url)]
        ]
    )
    await message.answer(
        "Hapni lidhjen dhe autorizoni Google Drive. Lidhja skadon pas 10 minutash.\n\n"
        "Pas autorizimit, përdorni /google_folder LINKU_DRIVE për projektin aktiv.",
        reply_markup=markup,
    )


@router.message(Command("google_status", "drive_status"))
async def google_status_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        connection = await google_drive_connection_status(session, user_id=user.id)
        if not connection.connected:
            await message.answer("Google Drive nuk është lidhur. Përdorni /google_connect.")
            return
        project = await get_active_project(session, user_id=user.id)
        binding = (
            await google_drive_binding_status(
                session,
                project_id=project.id,
                user_id=user.id,
            )
            if project is not None
            else None
        )
    identity = connection.email or connection.display_name or "llogari Google"
    lines = [f"Google Drive është lidhur me: {identity}"]
    if project is None:
        lines.append("Nuk ka projekt aktiv.")
    elif binding is None:
        lines.append(f"Projekti {project.name} nuk ka folder Drive të lidhur.")
    else:
        lines.extend(
            [
                f"Projekti aktiv: {project.name}",
                f"Folderi: {binding.folder_name}",
                f"Statusi i sinkronizimit: {binding.sync_status}",
            ]
        )
        if binding.last_sync_completed_at is not None:
            lines.append(
                f"Sinkronizimi i fundit: {binding.last_sync_completed_at.isoformat()}"
            )
    await message.answer("\n".join(lines))


@router.message(Command("google_folder", "drive_folder"))
async def google_folder_command(message: Message, command: CommandObject) -> None:
    folder_url = (command.args or "").strip()
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Zgjidhni ose krijoni një projekt aktiv më parë.")
            return
        if not folder_url:
            binding = await google_drive_binding_status(
                session,
                project_id=project.id,
                user_id=user.id,
            )
            if binding is None:
                await message.answer(
                    "Projekti nuk ka folder Drive të lidhur.\n\n"
                    "Përdorni: /google_folder LINKU_DRIVE"
                )
                return
            await message.answer(
                f"Projekti: {project.name}\n"
                f"Folderi: {binding.folder_name}\n"
                f"Linku: {binding.folder_url}\n"
                f"Statusi: {binding.sync_status}"
            )
            return
        try:
            binding = await bind_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user.id,
                folder_url=folder_url,
            )
        except GoogleDriveError as exc:
            await message.answer(str(exc))
            return
    await message.answer(
        f"Folderi u lidh me projektin {project.name}: {binding.folder_name}\n\n"
        "Përdorni /google_check për kontroll ose /google_sync për sinkronizim."
    )


@router.message(Command("google_check", "drive_check"))
async def google_check_command(message: Message, command: CommandObject) -> None:
    folder_url = (command.args or "").strip() or None
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Zgjidhni ose krijoni një projekt aktiv më parë.")
            return
        try:
            result = await preflight_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user.id,
                folder_url=folder_url,
            )
        except GoogleDriveError as exc:
            await message.answer(str(exc))
            return
    await message.answer(
        "Kontrolli i Google Drive\n\n"
        f"Folderi: {result.folder_name}\n"
        f"Lexim: {'Po' if result.readable else 'Jo'}\n"
        f"Shkrim: {'Po' if result.writable else 'Jo'}\n"
        f"Të reja: {result.new_count}\n"
        f"Të ndryshuara: {result.changed_count}\n"
        f"Të pandryshuara: {result.unchanged_count}\n"
        f"Të hequra: {result.deleted_count}\n"
        f"Të anashkaluara: {result.skipped_count}"
    )


@router.message(Command("google_sync", "drive_sync"))
async def google_sync_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Zgjidhni ose krijoni një projekt aktiv më parë.")
            return
        binding = await google_drive_binding_status(
            session,
            project_id=project.id,
            user_id=user.id,
        )
        if binding is None:
            await message.answer(
                "Projekti nuk ka folder Drive të lidhur. Përdorni /google_folder LINKU_DRIVE."
            )
            return
        run, created = await prompting_service.create_or_get_prompt_run(
            session,
            user_id=user.id,
            telegram_chat_id=message.chat.id,
            telegram_message_id=message.message_id,
            telegram_update_id=None,
            original_prompt="/google_sync",
        )
        if not created:
            return
        run.project_id = project.id
        plan = PromptPlan(
            version="prompt-plan-v1",
            language="sq-AL",
            needs_clarification=False,
            clarification_question=None,
            clarification_kind=None,
            clarification_options=[],
            actions=[
                PromptAction(
                    id="step-1",
                    type="sync_drive_folder",
                    arguments=PromptActionArguments(),
                    depends_on=[],
                    requires_confirmation=False,
                )
            ],
        )
        await prompting_service.save_prompt_plan(
            session,
            run=run,
            plan=plan,
            provider="deterministic",
            model="google-drive-sync",
            token_usage={},
        )
    run_prompt_workflow.send(str(run.id))
    await message.answer(
        f"Sinkronizimi i Google Drive u vendos në radhë për projektin: {project.name}"
    )


@router.message(Command("google_unbind", "drive_unbind"))
async def google_unbind_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        project = await get_active_project(session, user_id=user.id)
        if project is None:
            await message.answer("Nuk ka projekt aktiv.")
            return
        removed = await unbind_google_drive_folder(
            session,
            project_id=project.id,
            user_id=user.id,
        )
    if removed:
        await message.answer(
            "Folderi Drive u shkëput nga projekti. Dokumentet e importuara u ruajtën."
        )
    else:
        await message.answer("Projekti nuk kishte folder Drive të lidhur.")


@router.message(Command("google_disconnect", "drive_disconnect"))
async def google_disconnect_command(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        user = await get_or_create_message_user(session, message)
        deleted = await delete_google_drive_connection(session, user_id=user.id)
    if deleted:
        await message.answer(
            "Google Drive u shkëput nga bot-i. Mund ta hiqni edhe lejen e aplikacionit "
            "nga Google Account > Security > Third-party access."
        )
    else:
        await message.answer("Nuk kishte një lidhje aktive Google Drive.")
