import asyncio
import uuid

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.ai.service import get_user_ai_credentials
from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.document_analysis.service import analyze_file_version as analyze_file_version_service
from app.files.parse_service import parse_file_version as parse_file_version_service
from app.reviews.service import run_queued_review_job as run_queued_review_job_service

redis_broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(redis_broker)


@dramatiq.actor
def parse_file_version(file_version_id: str) -> None:
    asyncio.run(_parse_file_version(file_version_id))


async def _parse_file_version(file_version_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await parse_file_version_service(
                session,
                file_version_id=uuid.UUID(file_version_id),
            )
    finally:
        await engine.dispose()


@dramatiq.actor
def analyze_file_version(file_version_id: str, requested_by: str) -> None:
    asyncio.run(_analyze_file_version(file_version_id, requested_by))


async def _analyze_file_version(file_version_id: str, requested_by: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            user_id = uuid.UUID(requested_by)
            ai_settings = await get_user_ai_credentials(session, user_id=user_id)
            if ai_settings is None:
                raise RuntimeError("Përdoruesi nuk ka konfigurim aktiv AI.")
            await analyze_file_version_service(
                session,
                file_version_id=uuid.UUID(file_version_id),
                requested_by=user_id,
                ai_settings=ai_settings,
            )
    finally:
        await engine.dispose()


@dramatiq.actor
def run_review_job(review_job_id: str) -> None:
    asyncio.run(_run_review_job(review_job_id))


async def _run_review_job(review_job_id: str) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await run_queued_review_job_service(
                session,
                job_id=uuid.UUID(review_job_id),
            )
    finally:
        await engine.dispose()


@dramatiq.actor
def send_notification(notification_id: str) -> None:
    _ = notification_id
