from io import BytesIO
from pathlib import Path
from uuid import UUID

from aiogram.types import Message
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.files import service as files_service
from app.files.parser import is_supported_filename
from app.projects import service as projects_service
from app.projects.models import Project
from app.reviews.models import ReviewJob
from app.users import service as users_service
from app.users.models import User


class TelegramUpload:
    def __init__(self, *, filename: str, content_type: str | None, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._buffer = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


async def get_or_create_message_user(session: AsyncSession, message: Message) -> User:
    if message.from_user is None:
        raise ValueError("Telegram user is missing.")

    return await users_service.get_or_create_telegram_user(
        session,
        telegram_user_id=message.from_user.id,
        telegram_username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )


async def get_active_project(session: AsyncSession, *, user_id: UUID) -> Project | None:
    projects = await projects_service.list_projects(session, user_id=user_id)
    return projects[0] if projects else None


async def get_latest_review_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID,
) -> ReviewJob | None:
    result = await session.execute(
        select(ReviewJob)
        .where(
            ReviewJob.requested_by == user_id,
            ReviewJob.project_id == project_id,
        )
        .order_by(ReviewJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_latest_completed_review_job(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID,
) -> ReviewJob | None:
    result = await session.execute(
        select(ReviewJob)
        .where(
            ReviewJob.requested_by == user_id,
            ReviewJob.project_id == project_id,
            ReviewJob.status == "completed",
        )
        .order_by(ReviewJob.completed_at.desc(), ReviewJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def build_upload_from_message(message: Message) -> TelegramUpload:
    if message.document is None:
        raise ValueError("Telegram document is missing.")

    filename = message.document.file_name or "dokument"
    if not is_supported_filename(filename):
        raise HTTPException(status_code=400, detail="unsupported")

    destination = BytesIO()
    await message.bot.download(message.document, destination=destination)
    return TelegramUpload(
        filename=filename,
        content_type=message.document.mime_type,
        content=destination.getvalue(),
    )


async def store_telegram_document(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    upload: TelegramUpload,
) -> tuple[int, int]:
    suffix = Path(upload.filename).suffix.lower()
    if suffix == ".zip":
        uploaded, skipped = await files_service.import_project_files_zip(
            session,
            project_id=project_id,
            upload=upload,
            user_id=user_id,
        )
        return len(uploaded), len(skipped)

    await files_service.create_project_file(
        session,
        project_id=project_id,
        upload=upload,
        user_id=user_id,
    )
    return 1, 0
