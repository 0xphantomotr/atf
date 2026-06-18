from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.users.models import User


async def get_current_user(
    x_user_id: Annotated[UUID | None, Header(alias="X-User-Id")] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    if x_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mungon identifikuesi i përdoruesit.",
        )

    user = await session.get(User, x_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Përdoruesi nuk u gjet.",
        )
    return user

