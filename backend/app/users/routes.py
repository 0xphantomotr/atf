from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.users.schemas import TelegramUserCreate, UserRead
from app.users.service import get_or_create_telegram_user

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/telegram", response_model=UserRead)
async def create_or_get_telegram_user(
    payload: TelegramUserCreate,
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    user = await get_or_create_telegram_user(
        session,
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return UserRead.model_validate(user)
