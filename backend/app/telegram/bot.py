from aiogram import Bot, Dispatcher

from app.core.config import settings
from app.telegram.handlers import ai_settings, generate, projects, reports, start, status, uploads


def create_bot() -> Bot:
    return Bot(token=settings.telegram_bot_token)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start.router)
    dispatcher.include_router(ai_settings.router)
    dispatcher.include_router(projects.router)
    dispatcher.include_router(uploads.router)
    dispatcher.include_router(generate.router)
    dispatcher.include_router(status.router)
    dispatcher.include_router(reports.router)
    return dispatcher
