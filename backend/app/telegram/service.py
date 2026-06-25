from io import BytesIO
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import Message, User as TelegramUser
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.files import service as files_service
from app.files.parser import is_supported_filename
from app.projects import service as projects_service
from app.projects.models import Project
from app.reviews.models import ReviewJob
from app.users.models import TelegramAccount
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

    return await get_or_create_telegram_user(session, telegram_user=message.from_user)


async def get_or_create_telegram_user(
    session: AsyncSession,
    *,
    telegram_user: TelegramUser,
) -> User:
    return await users_service.get_or_create_telegram_user(
        session,
        telegram_user_id=telegram_user.id,
        telegram_username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
    )


async def get_active_project(session: AsyncSession, *, user_id: UUID) -> Project | None:
    account = await _get_telegram_account_for_user(session, user_id=user_id)
    if account and account.active_project_id:
        project = await _get_accessible_project_or_none(
            session,
            user_id=user_id,
            project_id=account.active_project_id,
        )
        if project is not None:
            return project
        account.active_project_id = None
        await session.commit()

    projects = await projects_service.list_projects(session, user_id=user_id)
    return projects[0] if projects else None


async def set_active_project(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID,
) -> Project:
    project = await projects_service.get_project(
        session,
        project_id=project_id,
        user_id=user_id,
    )
    account = await _get_telegram_account_for_user(session, user_id=user_id)
    if account is not None:
        account.active_project_id = project.id
        await session.commit()
    return project


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


def display_review_job_error(error_message: str | None) -> str:
    if error_message == "Stored secret could not be decrypted.":
        return (
            "API key i ruajtur nuk mund të lexohet me çelësin aktual të enkriptimit. "
            "Ruajeni përsëri me /ai_key provider api_key."
        )
    return error_message or "Gabim i panjohur."


def display_review_job_stage(stage: str | None) -> str | None:
    labels = {
        "queued": "Në radhë",
        "starting": "Duke nisur",
        "loading_project": "Duke lexuar projektin",
        "document_analysis": "Analiza e dokumenteve",
        "document_analysis_complete": "Analiza e dokumenteve përfundoi",
        "rules_and_findings": "Kontrolli ligjor/deterministik",
        "draft_generation": "Hartimi i Akt-Kolaudimit",
        "rendering": "Përgatitja e PDF",
        "completed": "Përfunduar",
        "failed": "Dështoi",
    }
    if not stage:
        return None
    return labels.get(stage, stage)


def display_retry_time(value) -> str:
    if value is None:
        return "pas pak"
    try:
        timezone_info = ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        timezone_info = ZoneInfo("Europe/Tirane")
    return value.astimezone(timezone_info).strftime("%d.%m.%Y %H:%M")


def document_progress_line(progress_details: dict | None) -> str | None:
    if not isinstance(progress_details, dict):
        return None
    documents = progress_details.get("documents")
    if not isinstance(documents, dict):
        return None
    total = int(documents.get("total") or 0)
    if total <= 0:
        return None
    analyzed = int(documents.get("analyzed") or 0)
    processed = int(documents.get("processed") or 0)
    current_filename = documents.get("current_filename")
    line = f"Dokumente: {analyzed}/{total} të analizuara, {processed}/{total} të kaluara"
    if isinstance(current_filename, str) and current_filename:
        line = f"{line}\nAktualisht: {current_filename[:120]}"
    return line


async def _get_telegram_account_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> TelegramAccount | None:
    result = await session.execute(
        select(TelegramAccount)
        .options(selectinload(TelegramAccount.user))
        .where(TelegramAccount.user_id == user_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_accessible_project_or_none(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID,
) -> Project | None:
    try:
        return await projects_service.get_project(
            session,
            project_id=project_id,
            user_id=user_id,
        )
    except HTTPException:
        return None


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
