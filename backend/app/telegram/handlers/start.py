from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.telegram.keyboards import start_keyboard
from app.telegram.messages import WELCOME_MESSAGE

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(WELCOME_MESSAGE, reply_markup=start_keyboard())

