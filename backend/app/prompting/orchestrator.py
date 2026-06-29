from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.projects.service import get_project
from app.prompting import service as prompting_service
from app.prompting.executor import PromptExecutionError, execute_prompt_plan
from app.prompting.models import PromptRun
from app.prompting.parsing import (
    format_prompt_parse_summary,
    load_prompt_parse_summary,
)
from app.prompting.policy import PromptPolicyError, validate_prompt_plan
from app.prompting.schemas import PromptPlan


@dataclass(frozen=True)
class PromptAdvanceResult:
    reschedule_after_seconds: int | None = None


async def advance_prompt_run(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> PromptAdvanceResult:
    run, claimed, retry_seconds = await prompting_service.claim_prompt_run(
        session,
        run_id=run_id,
        lease_seconds=settings.prompt_worker_lease_seconds,
    )
    if run is None:
        return PromptAdvanceResult()
    if not claimed:
        return PromptAdvanceResult(reschedule_after_seconds=retry_seconds)

    try:
        if run.status == "waiting_for_documents":
            return await _advance_document_wait(session, run=run)
        return await _execute_attachment_plan(session, run=run)
    except (PromptExecutionError, PromptPolicyError) as exc:
        code = exc.code
        detail = exc.user_message
    except HTTPException as exc:
        code = "attachment_service_error"
        detail = str(exc.detail)
    except ValidationError:
        code = "stored_plan_invalid"
        detail = "Plani i ruajtur i /prompt nuk është më i vlefshëm."

    await _fail_with_notification(
        session,
        run_id=run.id,
        code=code,
        detail=detail,
    )
    return PromptAdvanceResult()


async def _execute_attachment_plan(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> PromptAdvanceResult:
    plan = PromptPlan.model_validate(run.plan)
    validate_prompt_plan(plan, has_attachment=True)
    results = await execute_prompt_plan(
        session,
        run=run,
        plan=plan,
        user_id=run.user_id,
    )
    import_result = next(
        (result for result in results if result.action_type == "import_attachment"),
        None,
    )
    if import_result is None:
        raise PromptExecutionError(
            "attachment_import_missing",
            "Plani nuk prodhoi rezultat importimi.",
        )

    prompting_service.queue_prompt_notification(
        run,
        key="attachment_imported",
        body=import_result.message + "\n\nPo pres përfundimin e leximit të dokumenteve.",
    )
    await prompting_service.mark_prompt_run_waiting_for_documents(
        session,
        run=run,
        import_data=import_result.data,
    )
    return PromptAdvanceResult(
        reschedule_after_seconds=settings.prompt_parse_poll_seconds,
    )


async def _advance_document_wait(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> PromptAdvanceResult:
    metadata = dict(run.attachment_metadata or {})
    import_data = dict(metadata.get("import") or {})
    version_ids = _version_ids(import_data)
    if not version_ids:
        raise PromptExecutionError(
            "attachment_versions_missing",
            "Importimi nuk ruajti versionet e dokumenteve për monitorim.",
        )

    summary = await load_prompt_parse_summary(session, version_ids=version_ids)
    if not summary.complete:
        if _parse_wait_timed_out(metadata):
            await _fail_with_notification(
                session,
                run_id=run.id,
                code="document_parsing_timeout",
                detail="Përpunimi i dokumenteve tejkaloi kohën e lejuar.",
            )
            return PromptAdvanceResult()
        await prompting_service.release_prompt_run_lease(session, run=run)
        return PromptAdvanceResult(
            reschedule_after_seconds=settings.prompt_parse_poll_seconds,
        )

    if run.project_id is None:
        raise PromptExecutionError(
            "project_context_missing",
            "Projekti i importimit nuk u ruajt.",
        )
    project = await get_project(
        session,
        project_id=run.project_id,
        user_id=run.user_id,
    )
    body = format_prompt_parse_summary(
        summary,
        project_name=project.name,
        skipped_count=int(import_data.get("skipped_count") or 0),
    )
    prompting_service.queue_prompt_notification(
        run,
        key="document_parsing_completed",
        body=body,
    )
    await prompting_service.complete_prompt_run(session, run=run)
    return PromptAdvanceResult()


async def _fail_with_notification(
    session: AsyncSession,
    *,
    run_id: UUID,
    code: str,
    detail: str,
) -> None:
    await prompting_service.fail_prompt_run(
        session,
        run_id=run_id,
        code=code,
        detail=detail,
    )
    run = await session.get(PromptRun, run_id)
    if run is None:
        return
    prompting_service.queue_prompt_notification(
        run,
        key="prompt_run_failed",
        body=f"Kërkesa /prompt dështoi.\n\n{detail}",
    )
    await session.commit()


def _version_ids(import_data: dict) -> list[UUID]:
    values = import_data.get("file_version_ids")
    if not isinstance(values, list):
        return []
    version_ids: list[UUID] = []
    for value in values:
        try:
            version_ids.append(UUID(str(value)))
        except ValueError:
            continue
    return version_ids


def _parse_wait_timed_out(metadata: dict) -> bool:
    value = metadata.get("parse_wait_started_at")
    if not isinstance(value, str):
        return False
    try:
        started_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    return elapsed >= settings.prompt_parse_timeout_seconds
