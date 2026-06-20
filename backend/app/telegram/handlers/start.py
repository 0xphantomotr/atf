from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.db.session import AsyncSessionLocal
from app.telegram.keyboards import start_keyboard
from app.telegram.messages import WELCOME_MESSAGE
from app.telegram.service import get_or_create_message_user

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        await get_or_create_message_user(session, message)
    await message.answer(WELCOME_MESSAGE, reply_markup=start_keyboard())


@router.message(Command("ndihme", "help"))
async def help_command(message: Message) -> None:
    await message.answer(_help_text(), reply_markup=start_keyboard())


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer(_help_text(), reply_markup=start_keyboard())
    await callback.answer()


def _help_text() -> str:
    return (
        "Komandat kryesore:\n\n"
        "/projekt_ri Emri i projektit - krijon projekt të ri\n"
        "/projektet - shfaq projektet\n"
        "/ngarko - udhëzim për ngarkim dokumentesh\n"
        "/dokumentet - shfaq dokumentet e projektit aktiv\n"
        "/ai - shfaq ose konfiguroni AI provider/model\n"
        "/ai_key provider api_key - ruan API key personale\n"
        "/ai_models - shfaq modelet e provider-it\n"
        "/ai_model model - zgjedh modelin aktiv\n"
        "/ai_delete - fshin API key personale\n"
        "/gjenero - nis auditimin VKM 610/2022\n"
        "/status - kontrollon statusin e auditimit\n"
        "/raportet - dërgon raportin PDF të fundit\n\n"
        "Projekti aktiv është projekti më i fundit i krijuar."
    )
