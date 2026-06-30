from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.core.config import settings
from app.prompting.models import PromptRun, PromptRunStep
from app.prompting.schemas import PromptClarificationContext, PromptRecentTurn


@dataclass(frozen=True)
class QAFollowUpContext:
    question: str
    answer: str
    certainty: str
    evidence_ids: list[str]
    follow_up_suggestion: str | None = None


async def load_recent_prompt_turns(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
    project_id: UUID | None,
    exclude_run_id: UUID,
    limit: int = 3,
) -> list[PromptRecentTurn]:
    if project_id is None:
        return []
    result = await session.execute(
        select(PromptRun)
        .where(
            PromptRun.user_id == user_id,
            PromptRun.telegram_chat_id == telegram_chat_id,
            PromptRun.project_id == project_id,
            PromptRun.id != exclude_run_id,
            PromptRun.status == "completed",
        )
        .order_by(PromptRun.completed_at.desc())
        .limit(max(1, min(limit, 5)))
    )
    turns: list[PromptRecentTurn] = []
    for run in reversed(list(result.scalars())):
        actions = run.plan.get("actions") if isinstance(run.plan, dict) else []
        action_types = [
            str(action.get("type"))
            for action in actions or []
            if isinstance(action, dict) and action.get("type")
        ]
        turns.append(
            PromptRecentTurn(
                request=run.original_prompt[:500],
                action_types=action_types[:8],
            )
        )
    return turns


async def load_pending_clarification(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
    exclude_run_id: UUID,
) -> tuple[PromptRun | None, PromptClarificationContext | None]:
    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.prompt_clarification_timeout_seconds
    )
    result = await session.execute(
        select(PromptRun)
        .where(
            PromptRun.user_id == user_id,
            PromptRun.telegram_chat_id == telegram_chat_id,
            PromptRun.id != exclude_run_id,
            PromptRun.status == "waiting_for_clarification",
            PromptRun.created_at >= cutoff,
        )
        .order_by(PromptRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None, None
    pending = dict(run.pending_clarification or {})
    try:
        context = PromptClarificationContext(
            original_request=run.original_prompt[:1_500],
            kind=str(pending.get("kind") or "action"),
            question=str(pending.get("question") or "Ju lutem sqaroni kërkesën."),
            options=[str(value) for value in pending.get("options") or []][:8],
        )
    except ValueError:
        return None, None
    return run, context


async def resolve_pending_clarification(
    session: AsyncSession,
    *,
    pending_run: PromptRun,
    resolved_by_run_id: UUID,
) -> None:
    pending_run.status = "cancelled"
    pending_run.error_code = "clarification_resolved"
    pending_run.error_detail = None
    pending_run.pending_clarification = {
        "resolved_by_run_id": str(resolved_by_run_id),
    }
    pending_run.completed_at = datetime.now(timezone.utc)
    await write_audit_log(
        session,
        action="prompt.clarification.resolved",
        entity_type="prompt_run",
        entity_id=pending_run.id,
        actor_user_id=pending_run.user_id,
        project_id=pending_run.project_id,
        details={"resolved_by_run_id": str(resolved_by_run_id)},
    )
    await session.commit()


async def cancel_pending_clarification(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
) -> bool:
    result = await session.execute(
        select(PromptRun)
        .where(
            PromptRun.user_id == user_id,
            PromptRun.telegram_chat_id == telegram_chat_id,
            PromptRun.status == "waiting_for_clarification",
        )
        .order_by(PromptRun.created_at.desc())
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return False
    run.status = "cancelled"
    run.error_code = "clarification_cancelled"
    run.pending_clarification = {}
    run.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def load_qa_follow_up_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    telegram_chat_id: int,
    project_id: UUID,
    exclude_run_id: UUID,
) -> QAFollowUpContext | None:
    result = await session.execute(
        select(PromptRun, PromptRunStep)
        .join(PromptRunStep, PromptRunStep.prompt_run_id == PromptRun.id)
        .where(
            PromptRun.user_id == user_id,
            PromptRun.telegram_chat_id == telegram_chat_id,
            PromptRun.project_id == project_id,
            PromptRun.id != exclude_run_id,
            PromptRun.status == "completed",
            PromptRunStep.action_type == "answer_project_question",
            PromptRunStep.status == "completed",
        )
        .order_by(PromptRunStep.completed_at.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    prior_run, step = row
    data = dict((step.result_data or {}).get("data") or {})
    answer = data.get("answer")
    certainty = data.get("certainty")
    if not isinstance(answer, str) or not isinstance(certainty, str):
        return None
    return QAFollowUpContext(
        question=prior_run.original_prompt[:1_000],
        answer=answer[:1_500],
        certainty=certainty,
        evidence_ids=[str(value) for value in data.get("evidence_ids") or []][:8],
        follow_up_suggestion=(
            str(data["follow_up_suggestion"])[:500]
            if data.get("follow_up_suggestion")
            else None
        ),
    )


def clarification_message(
    question: str,
    *,
    options: list[str],
) -> str:
    lines = [question]
    if options:
        lines.extend(["", "Mundësitë:"])
        lines.extend(f"{index}. {option}" for index, option in enumerate(options, start=1))
    lines.extend(["", "Përgjigjuni me /prompt dhe sqarimin tuaj."])
    return "\n".join(lines)
