from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db.session import AsyncSessionLocal
from app.google_drive.oauth import (
    GoogleDriveAuthError,
    create_google_authorization_url,
    delete_google_drive_connection,
    google_drive_connection_status,
    google_drive_is_configured,
)
from app.telegram.service import get_or_create_message_user

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
        "Pas autorizimit, kthehuni këtu dhe përdorni /prompt me linkun e folderit.",
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
    identity = connection.email or connection.display_name or "llogari Google"
    await message.answer(f"Google Drive është lidhur me: {identity}")


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
