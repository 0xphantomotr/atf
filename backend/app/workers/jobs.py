import asyncio
import uuid

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.files.parse_service import parse_file_version as parse_file_version_service

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
def run_review_job(review_job_id: str) -> None:
    _ = review_job_id


@dramatiq.actor
def send_notification(notification_id: str) -> None:
    _ = notification_id
