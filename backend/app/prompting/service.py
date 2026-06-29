from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.prompting.models import PromptRun, PromptRunStep
from app.prompting.schemas import PromptPlan


async def create_or_get_prompt_run(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_update_id: int | None,
    original_prompt: str,
) -> tuple[PromptRun, bool]:
    existing = await _find_prompt_run(
        session,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_update_id=telegram_update_id,
    )
    if existing is not None:
        return existing, False

    run = PromptRun(
        user_id=user_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        telegram_update_id=telegram_update_id,
        status="planning",
        original_prompt=original_prompt,
        plan={},
        planner_metadata={},
        attachment_metadata={},
        pending_clarification={},
    )
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_prompt_run(
            session,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            telegram_update_id=telegram_update_id,
        )
        if existing is None:
            raise
        return existing, False
    await session.refresh(run)
    return run, True


async def save_prompt_plan(
    session: AsyncSession,
    *,
    run: PromptRun,
    plan: PromptPlan,
    provider: str,
    model: str,
    token_usage: dict[str, int],
) -> None:
    run.plan_version = plan.version
    run.plan = plan.model_dump(mode="json")
    run.planner_metadata = {
        "provider": provider,
        "model": model,
        "token_usage": token_usage,
    }
    run.status = "waiting_for_clarification" if plan.needs_clarification else "queued"
    run.pending_clarification = (
        {"question": plan.clarification_question} if plan.needs_clarification else {}
    )
    await write_audit_log(
        session,
        action="prompt.plan.accepted",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={
            "plan_version": plan.version,
            "actions": [action.type for action in plan.actions],
            "needs_clarification": plan.needs_clarification,
            "provider": provider,
            "model": model,
            "token_usage": token_usage,
        },
    )
    await session.commit()


async def claim_prompt_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_seconds: int,
) -> tuple[PromptRun | None, bool, int | None]:
    now = datetime.now(timezone.utc)
    lease_until = now + timedelta(seconds=lease_seconds)
    result = await session.execute(
        update(PromptRun)
        .where(
            PromptRun.id == run_id,
            PromptRun.status.notin_(
                {
                    "completed",
                    "failed",
                    "cancelled",
                    "waiting_for_clarification",
                    "waiting_for_confirmation",
                }
            ),
            or_(
                PromptRun.worker_lease_until.is_(None),
                PromptRun.worker_lease_until <= now,
            ),
        )
        .values(
            worker_lease_until=lease_until,
            worker_attempt_count=PromptRun.worker_attempt_count + 1,
        )
        .returning(PromptRun)
    )
    run = result.scalar_one_or_none()
    if run is not None:
        await session.commit()
        return run, True, None

    await session.rollback()
    run = await session.get(PromptRun, run_id)
    if run is None or run.worker_lease_until is None:
        return run, False, None
    retry_seconds = max(
        1,
        int((run.worker_lease_until - now).total_seconds()) + 1,
    )
    return run, False, retry_seconds


async def start_prompt_step(
    session: AsyncSession,
    *,
    run: PromptRun,
    step_key: str,
    action_type: str,
    arguments: dict,
) -> tuple[PromptRunStep, bool]:
    step = await get_prompt_step(session, run_id=run.id, step_key=step_key)
    if step is not None and step.status == "completed":
        return step, False
    if step is None:
        step = PromptRunStep(
            prompt_run_id=run.id,
            step_key=step_key,
            action_type=action_type,
            status="running",
            arguments=arguments,
            result_data={},
            attempt_count=1,
            started_at=datetime.now(timezone.utc),
        )
        session.add(step)
    else:
        step.status = "running"
        step.attempt_count += 1
        step.started_at = datetime.now(timezone.utc)
        step.completed_at = None
        step.error_code = None
        step.error_detail = None
    run.status = "running"
    await session.commit()
    await session.refresh(step)
    return step, True


async def complete_prompt_step(
    session: AsyncSession,
    *,
    run: PromptRun,
    step: PromptRunStep,
    result: dict,
    project_id: UUID | None,
    review_job_id: UUID | None = None,
) -> None:
    step.status = "completed"
    step.result_data = result
    step.completed_at = datetime.now(timezone.utc)
    if project_id is not None:
        run.project_id = project_id
    if review_job_id is not None:
        run.review_job_id = review_job_id
    result_data = result.get("data")
    safe_result_data = result_data if isinstance(result_data, dict) else {}
    await write_audit_log(
        session,
        action="prompt.action.completed",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={
            "step_key": step.step_key,
            "action_type": step.action_type,
            "result_refs": {
                key: value
                for key, value in safe_result_data.items()
                if key.endswith("_id") or key in {"count", "status"}
            },
        },
    )
    await session.commit()


async def fail_prompt_step(
    session: AsyncSession,
    *,
    step_id: UUID,
    code: str,
    detail: str,
) -> None:
    await session.rollback()
    result = await session.execute(select(PromptRunStep).where(PromptRunStep.id == step_id))
    step = result.scalar_one_or_none()
    if step is None:
        return
    step.status = "failed"
    step.error_code = code
    step.error_detail = detail[:2_000]
    step.completed_at = datetime.now(timezone.utc)
    run_result = await session.execute(
        select(PromptRun).where(PromptRun.id == step.prompt_run_id)
    )
    run = run_result.scalar_one()
    await write_audit_log(
        session,
        action="prompt.action.failed",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={
            "step_key": step.step_key,
            "action_type": step.action_type,
            "error_code": code,
        },
    )
    await session.commit()


async def complete_prompt_run(session: AsyncSession, *, run: PromptRun) -> None:
    run.status = "completed"
    run.error_code = None
    run.error_detail = None
    run.worker_lease_until = None
    run.completed_at = datetime.now(timezone.utc)
    await write_audit_log(
        session,
        action="prompt.run.completed",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={"plan_version": run.plan_version},
    )
    await session.commit()


async def fail_prompt_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    code: str,
    detail: str,
) -> None:
    await session.rollback()
    result = await session.execute(select(PromptRun).where(PromptRun.id == run_id))
    run = result.scalar_one()
    run.status = "failed"
    run.error_code = code
    run.error_detail = detail[:2_000]
    run.worker_lease_until = None
    run.completed_at = datetime.now(timezone.utc)
    await write_audit_log(
        session,
        action="prompt.run.failed",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={"error_code": code},
    )
    await session.commit()


async def mark_prompt_run_waiting_for_documents(
    session: AsyncSession,
    *,
    run: PromptRun,
    import_data: dict,
) -> None:
    metadata = dict(run.attachment_metadata or {})
    metadata.setdefault("parse_wait_started_at", datetime.now(timezone.utc).isoformat())
    metadata["import"] = import_data
    run.attachment_metadata = metadata
    run.status = "waiting_for_documents"
    run.worker_lease_until = None
    await session.commit()


async def mark_prompt_run_waiting_for_confirmation(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> None:
    run.status = "waiting_for_confirmation"
    run.worker_lease_until = None
    await session.commit()


async def mark_prompt_run_waiting_for_review(
    session: AsyncSession,
    *,
    run: PromptRun,
    review_job_id: UUID,
) -> None:
    run.review_job_id = review_job_id
    run.status = "waiting_for_review"
    run.worker_lease_until = None
    await session.commit()


async def mark_prompt_run_waiting_for_delivery(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> None:
    run.status = "waiting_for_delivery"
    run.worker_lease_until = None
    await session.commit()


async def release_prompt_run_lease(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> None:
    run.worker_lease_until = None
    await session.commit()


def queue_prompt_notification(
    run: PromptRun,
    *,
    key: str,
    body: str,
    kind: str = "text",
    data: dict | None = None,
) -> None:
    metadata = dict(run.attachment_metadata or {})
    notifications = dict(metadata.get("notifications") or {})
    if key not in notifications:
        sequence = max(
            (
                int(item.get("sequence") or 0)
                for item in notifications.values()
                if isinstance(item, dict)
            ),
            default=0,
        ) + 1
        notifications[key] = {
            "status": "pending",
            "kind": kind,
            "body": body,
            "data": data or {},
            "sequence": sequence,
        }
    metadata["notifications"] = notifications
    run.attachment_metadata = metadata


@dataclass(frozen=True)
class PromptNotification:
    key: str
    kind: str
    body: str
    data: dict
    sequence: int


def pending_prompt_notifications(run: PromptRun) -> list[PromptNotification]:
    metadata = dict(run.attachment_metadata or {})
    notifications = dict(metadata.get("notifications") or {})
    pending: list[PromptNotification] = []
    for key, value in notifications.items():
        item = dict(value) if isinstance(value, dict) else {}
        body = item.get("body")
        if item.get("status") == "pending" and isinstance(body, str) and body:
            pending.append(
                PromptNotification(
                    key=str(key),
                    kind=str(item.get("kind") or "text"),
                    body=body,
                    data=(
                        dict(item.get("data"))
                        if isinstance(item.get("data"), dict)
                        else {}
                    ),
                    sequence=int(item.get("sequence") or 0),
                )
            )
    return sorted(
        pending,
        key=lambda notification: (
            notification.sequence if notification.sequence > 0 else 2**31,
            notification.key,
        ),
    )


async def mark_prompt_notification_sent(
    session: AsyncSession,
    *,
    run_id: UUID,
    key: str,
    telegram_message_id: int,
) -> None:
    run = await session.get(PromptRun, run_id)
    if run is None:
        return
    metadata = dict(run.attachment_metadata or {})
    notifications = dict(metadata.get("notifications") or {})
    item = dict(notifications.get(key) or {})
    if not item:
        return
    item["status"] = "sent"
    item["telegram_message_id"] = telegram_message_id
    item["sent_at"] = datetime.now(timezone.utc).isoformat()
    notifications[key] = item
    metadata["notifications"] = notifications
    run.attachment_metadata = metadata
    await session.commit()


async def finalize_prompt_delivery(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> None:
    run = await session.get(PromptRun, run_id)
    if run is None or run.status != "waiting_for_delivery":
        return
    metadata = dict(run.attachment_metadata or {})
    notifications = dict(metadata.get("notifications") or {})
    delivery = dict(notifications.get("report_delivery") or {})
    if delivery.get("status") != "sent":
        return
    await complete_prompt_run(session, run=run)


async def record_prompt_notification_failure(
    session: AsyncSession,
    *,
    run_id: UUID,
    detail: str,
    max_attempts: int = 5,
) -> bool:
    await session.rollback()
    run = await session.get(PromptRun, run_id)
    if run is None:
        return True
    metadata = dict(run.attachment_metadata or {})
    attempts = int(metadata.get("notification_delivery_attempts") or 0) + 1
    metadata["notification_delivery_attempts"] = attempts
    metadata["notification_delivery_error"] = detail[:1_000]
    run.attachment_metadata = metadata
    terminal = attempts >= max_attempts
    if terminal:
        report_delivery = run.status == "waiting_for_delivery"
        run.status = "failed"
        run.error_code = (
            "report_delivery_failed"
            if report_delivery
            else "telegram_notification_failed"
        )
        run.error_detail = (
            (
                "Akt-Kolaudimi u gjenerua, por dërgimi automatik në Telegram dështoi. "
                "Përdorni /raportet për ta marrë përsëri."
            )
            if report_delivery
            else "Njoftimi automatik në Telegram dështoi pas disa tentativash."
        )
        run.completed_at = datetime.now(timezone.utc)
        run.worker_lease_until = None
        await write_audit_log(
            session,
            action=(
                "prompt.report.delivery_failed"
                if report_delivery
                else "prompt.notification.delivery_failed"
            ),
            entity_type="prompt_run",
            entity_id=run.id,
            actor_user_id=run.user_id,
            project_id=run.project_id,
            details={"attempts": attempts},
        )
    await session.commit()
    return terminal


async def get_latest_prompt_run(
    session: AsyncSession,
    *,
    user_id: UUID,
    project_id: UUID | None = None,
    telegram_chat_id: int | None = None,
    statuses: set[str] | None = None,
) -> PromptRun | None:
    query = select(PromptRun).where(PromptRun.user_id == user_id)
    if project_id is not None:
        query = query.where(PromptRun.project_id == project_id)
    if telegram_chat_id is not None:
        query = query.where(PromptRun.telegram_chat_id == telegram_chat_id)
    if statuses:
        query = query.where(PromptRun.status.in_(statuses))
    result = await session.execute(query.order_by(PromptRun.created_at.desc()).limit(1))
    return result.scalar_one_or_none()


async def get_prompt_step(
    session: AsyncSession,
    *,
    run_id: UUID,
    step_key: str,
) -> PromptRunStep | None:
    result = await session.execute(
        select(PromptRunStep).where(
            PromptRunStep.prompt_run_id == run_id,
            PromptRunStep.step_key == step_key,
        )
    )
    return result.scalar_one_or_none()


async def _find_prompt_run(
    session: AsyncSession,
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    telegram_update_id: int | None,
) -> PromptRun | None:
    conditions = [
        (
            PromptRun.telegram_chat_id == telegram_chat_id
        )
        & (PromptRun.telegram_message_id == telegram_message_id)
    ]
    if telegram_update_id is not None:
        conditions.append(PromptRun.telegram_update_id == telegram_update_id)
    result = await session.execute(select(PromptRun).where(or_(*conditions)).limit(1))
    return result.scalar_one_or_none()
