from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.security import constant_time_equals

router = APIRouter(prefix="/telegram", tags=["telegram"])


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

    # Dispatcher wiring is added in the Telegram MVP phase.
    _ = await request.json()
    return JSONResponse({"ok": True})

