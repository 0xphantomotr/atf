import asyncio
import uuid

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.ai.service import get_user_ai_credentials
from app.core.config import settings
from app.core.logging import get_logger
from app.db import models  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.document_analysis.service import analyze_file_version as analyze_file_version_service
from app.files.parse_service import parse_file_version as parse_file_version_service
from app.reviews.service import run_queued_review_job as run_queued_review_job_service

redis_broker = RedisBroker(url=settings.redis_url)
dramatiq.set_broker(redis_broker)
logger = get_logger(__name__)


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
        try:
            async with AsyncSessionLocal() as session:
                await run_queued_review_job_service(
                    session,
                    job_id=uuid.UUID(review_job_id),
                )
        except Exception as exc:
            logger.exception(
                "review_job_terminal_failure",
                review_job_id=review_job_id,
                error_type=type(exc).__name__,
            )
    finally:
        await engine.dispose()


@dramatiq.actor(max_retries=5, min_backoff=5_000, max_backoff=60_000)
def run_prompt_workflow(prompt_run_id: str) -> None:
    asyncio.run(_run_prompt_workflow(prompt_run_id))


async def _run_prompt_workflow(prompt_run_id: str) -> None:
    run_id = uuid.UUID(prompt_run_id)
    try:
        from app.prompting.orchestrator import advance_prompt_run

        async with AsyncSessionLocal() as session:
            outcome = await advance_prompt_run(session, run_id=run_id)
        try:
            await _deliver_prompt_notifications(run_id)
        except Exception as exc:
            from app.prompting.service import record_prompt_notification_failure

            async with AsyncSessionLocal() as session:
                terminal = await record_prompt_notification_failure(
                    session,
                    run_id=run_id,
                    detail=str(exc),
                )
            if not terminal:
                raise
            return
        if outcome.reschedule_after_seconds is not None:
            run_prompt_workflow.send_with_options(
                args=(prompt_run_id,),
                delay=outcome.reschedule_after_seconds * 1_000,
            )
    finally:
        await engine.dispose()


async def _deliver_prompt_notifications(run_id: uuid.UUID) -> None:
    from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

    from app.prompting.confirmation import confirmation_callback_data
    from app.prompting.generation import report_output_matches_prompt_job
    from app.prompting.models import PromptRun
    from app.prompting.service import (
        finalize_prompt_delivery,
        mark_prompt_notification_sent,
        pending_prompt_notifications,
    )
    from app.reviews import service as reviews_service
    from app.telegram.bot import create_bot

    async with AsyncSessionLocal() as session:
        run = await session.get(PromptRun, run_id)
        if run is None:
            return
        pending = pending_prompt_notifications(run)
        chat_id = run.telegram_chat_id

    if not pending:
        return

    bot = create_bot()
    try:
        for notification in pending:
            if notification.kind == "confirmation":
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Konfirmo gjenerimin",
                                callback_data=confirmation_callback_data(
                                    run,
                                    action="confirm",
                                ),
                            ),
                            InlineKeyboardButton(
                                text="Anulo",
                                callback_data=confirmation_callback_data(
                                    run,
                                    action="cancel",
                                ),
                            ),
                        ]
                    ]
                )
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=notification.body,
                    reply_markup=markup,
                )
            elif notification.kind == "document":
                output_id = uuid.UUID(str(notification.data["output_id"]))
                expected_job_id = uuid.UUID(str(notification.data["review_job_id"]))
                async with AsyncSessionLocal() as session:
                    output = await reviews_service.get_generated_output(
                        session,
                        output_id=output_id,
                        user_id=run.user_id,
                    )
                    if not report_output_matches_prompt_job(
                        prompt_review_job_id=run.review_job_id,
                        expected_review_job_id=expected_job_id,
                        output_review_job_id=output.review_job_id,
                    ):
                        raise RuntimeError(
                            "Prompt report output does not match the linked review job."
                        )
                    download = await reviews_service.download_generated_output(
                        session,
                        output_id=output.id,
                        user_id=run.user_id,
                    )
                sent = await bot.send_document(
                    chat_id=chat_id,
                    document=BufferedInputFile(
                        download.content,
                        filename=download.filename,
                    ),
                    caption=notification.body,
                )
            else:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=notification.body,
                )
            async with AsyncSessionLocal() as session:
                await mark_prompt_notification_sent(
                    session,
                    run_id=run_id,
                    key=notification.key,
                    telegram_message_id=sent.message_id,
                )
        async with AsyncSessionLocal() as session:
            await finalize_prompt_delivery(session, run_id=run_id)
    finally:
        await bot.session.close()


@dramatiq.actor
def send_notification(notification_id: str) -> None:
    _ = notification_id
