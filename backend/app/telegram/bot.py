from aiogram import Bot, Dispatcher

from app.core.config import settings


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def create_dispatcher() -> Dispatcher:
    return Dispatcher()

