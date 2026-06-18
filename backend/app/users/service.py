from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.users.models import TelegramAccount, User


async def get_or_create_telegram_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    telegram_username: str | None,
    first_name: str | None,
    last_name: str | None,
) -> User:
    result = await session.execute(
        select(TelegramAccount)
        .options(selectinload(TelegramAccount.user))
        .where(TelegramAccount.telegram_user_id == telegram_user_id)
    )
    account = result.scalar_one_or_none()
    if account:
        account.telegram_username = telegram_username
        account.first_name = first_name
        account.last_name = last_name
        await session.commit()
        return account.user

    display_name = " ".join(part for part in [first_name, last_name] if part) or telegram_username
    user = User(display_name=display_name, language="sq-AL")
    session.add(user)
    await session.flush()

    session.add(
        TelegramAccount(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            first_name=first_name,
            last_name=last_name,
        )
    )
    await session.commit()
    await session.refresh(user)
    return user
