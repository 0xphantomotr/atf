from functools import lru_cache

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.security import constant_time_equals
from app.telegram.bot import create_bot, create_dispatcher

router = APIRouter(prefix="/telegram", tags=["telegram"])


@lru_cache
def get_telegram_bot() -> Bot:
    return create_bot()


@lru_cache
def get_telegram_dispatcher() -> Dispatcher:
    return create_dispatcher()


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    telegram_secret: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
) -> JSONResponse:
    if not constant_time_equals(telegram_secret, settings.telegram_webhook_secret):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Telegram webhook secret is invalid."},
        )

    bot = get_telegram_bot()
    dispatcher = get_telegram_dispatcher()
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return JSONResponse({"ok": True})
