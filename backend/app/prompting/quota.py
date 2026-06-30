from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.prompting.models import PromptRun, PromptRunStep


class PromptQuotaError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


@dataclass(frozen=True)
class PromptQuotaUsage:
    requests_in_window: int
    requests_in_day: int
    ai_tokens_in_day: int


def evaluate_prompt_quota(
    usage: PromptQuotaUsage,
    *,
    max_requests_per_window: int,
    max_requests_per_day: int,
    max_ai_tokens_per_day: int,
) -> None:
    if (
        max_requests_per_window > 0
        and usage.requests_in_window > max_requests_per_window
    ):
        raise PromptQuotaError(
            "prompt_rate_limited",
            "Keni dërguar shumë kërkesa /prompt. Prisni pak dhe provoni përsëri.",
        )
    if max_requests_per_day > 0 and usage.requests_in_day > max_requests_per_day:
        raise PromptQuotaError(
            "prompt_daily_request_quota",
            "Keni arritur kufirin ditor të kërkesave /prompt.",
        )
    if max_ai_tokens_per_day > 0 and usage.ai_tokens_in_day >= max_ai_tokens_per_day:
        raise PromptQuotaError(
            "prompt_daily_token_quota",
            "Keni arritur kufirin ditor të përdorimit AI për /prompt.",
        )


async def enforce_prompt_quota(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> PromptQuotaUsage:
    now = datetime.now(timezone.utc)
    day_cutoff = now - timedelta(days=1)
    window_cutoff = now - timedelta(seconds=settings.prompt_rate_limit_window_seconds)
    result = await session.execute(
        select(PromptRun)
        .where(
            PromptRun.user_id == user_id,
            PromptRun.created_at >= day_cutoff,
        )
        .order_by(PromptRun.created_at.desc())
    )
    runs = list(result.scalars())
    run_ids = [run.id for run in runs]
    steps: list[PromptRunStep] = []
    if run_ids:
        step_result = await session.execute(
            select(PromptRunStep).where(PromptRunStep.prompt_run_id.in_(run_ids))
        )
        steps = list(step_result.scalars())

    usage = PromptQuotaUsage(
        requests_in_window=sum(1 for run in runs if run.created_at >= window_cutoff),
        requests_in_day=len(runs),
        ai_tokens_in_day=(
            sum(_usage_tokens((run.planner_metadata or {}).get("token_usage")) for run in runs)
            + sum(
                _usage_tokens(
                    ((step.result_data or {}).get("data") or {}).get("token_usage")
                )
                for step in steps
            )
        ),
    )
    evaluate_prompt_quota(
        usage,
        max_requests_per_window=settings.prompt_rate_limit_max_requests,
        max_requests_per_day=settings.prompt_daily_max_requests,
        max_ai_tokens_per_day=settings.prompt_daily_max_ai_tokens,
    )
    return usage


def _usage_tokens(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    total = value.get("total_tokens")
    if isinstance(total, int) and total >= 0:
        return total
    return sum(
        int(value.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens")
        if isinstance(value.get(key), int) and value.get(key) >= 0
    )
