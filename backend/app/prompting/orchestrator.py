from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.projects.service import get_project
from app.prompting import service as prompting_service
from app.prompting.confirmation import (
    PromptConfirmationError,
    prepare_generation_confirmation,
)
from app.prompting.executor import PromptExecutionError, execute_prompt_action
from app.prompting.models import PromptRun
from app.prompting.parsing import (
    format_prompt_parse_summary,
    load_prompt_parse_summary,
)
from app.prompting.policy import PromptPolicyError, validate_prompt_plan
from app.prompting.schemas import PromptAction, PromptPlan
from app.reviews import service as reviews_service
from app.telegram.service import display_review_job_error, display_retry_time


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
        if run.status == "waiting_for_review":
            return await _advance_review_wait(session, run=run)
        if run.status == "waiting_for_delivery":
            await prompting_service.release_prompt_run_lease(session, run=run)
            return PromptAdvanceResult()
        return await _advance_plan(session, run=run)
    except (PromptExecutionError, PromptPolicyError, PromptConfirmationError) as exc:
        code = exc.code
        detail = exc.user_message
    except HTTPException as exc:
        code = "prompt_service_error"
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


async def _advance_plan(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> PromptAdvanceResult:
    plan = PromptPlan.model_validate(run.plan)
    has_attachment = bool((run.attachment_metadata or {}).get("storage_path"))
    validate_prompt_plan(plan, has_attachment=has_attachment)

    for action in plan.actions:
        step = await prompting_service.get_prompt_step(
            session,
            run_id=run.id,
            step_key=action.id,
        )
        if step is not None and step.status == "completed":
            continue
        await _ensure_dependencies_completed(session, run=run, action=action)

        if action.type == "generate_kolaudim" and run.confirmed_at is None:
            estimate_step = await _estimate_dependency_step(
                session,
                run=run,
                action=action,
            )
            estimate_result = dict(estimate_step.result_data or {})
            estimate_data = dict(estimate_result.get("data") or {})
            fingerprint = estimate_data.get("estimate_fingerprint")
            estimate_message = estimate_result.get("message")
            if not isinstance(fingerprint, str) or not isinstance(estimate_message, str):
                raise PromptExecutionError(
                    "generation_estimate_missing",
                    "Vlerësimi paraprak nuk u ruajt siç duhet.",
                )
            await prepare_generation_confirmation(
                session,
                run=run,
                estimate_step_key=estimate_step.step_key,
                estimate_fingerprint=fingerprint,
                estimate_message=estimate_message,
            )
            return PromptAdvanceResult()

        result = await execute_prompt_action(
            session,
            run=run,
            action=action,
            user_id=run.user_id,
        )

        if action.type == "import_attachment":
            prompting_service.queue_prompt_notification(
                run,
                key="attachment_imported",
                body=result.message + "\n\nPo pres përfundimin e leximit të dokumenteve.",
            )
            await prompting_service.mark_prompt_run_waiting_for_documents(
                session,
                run=run,
                import_data=result.data,
            )
            return PromptAdvanceResult(
                reschedule_after_seconds=settings.prompt_parse_poll_seconds,
            )

        if action.type == "generate_kolaudim":
            review_job_id = _uuid_from_data(result.data, "review_job_id")
            prompting_service.queue_prompt_notification(
                run,
                key="generation_started",
                body=(
                    f"{result.message}\n\n"
                    "Do t'ju njoftoj dhe do t'ju dërgoj PDF-në kur të përfundojë."
                ),
            )
            await prompting_service.mark_prompt_run_waiting_for_review(
                session,
                run=run,
                review_job_id=review_job_id,
            )
            return PromptAdvanceResult(
                reschedule_after_seconds=settings.prompt_review_poll_seconds,
            )

        if action.type == "deliver_latest_report":
            prompting_service.queue_prompt_notification(
                run,
                key="report_delivery",
                kind="document",
                body=f"Akt Kolaudimi për projektin: {result.data.get('project_name', '')}",
                data={
                    "output_id": result.data.get("output_id"),
                    "review_job_id": result.data.get("review_job_id"),
                },
            )
            await prompting_service.mark_prompt_run_waiting_for_delivery(
                session,
                run=run,
            )
            return PromptAdvanceResult()

    prompting_service.queue_prompt_notification(
        run,
        key="prompt_run_completed",
        body="Kërkesa /prompt përfundoi me sukses.",
    )
    await prompting_service.complete_prompt_run(session, run=run)
    return PromptAdvanceResult()


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
    prompting_service.queue_prompt_notification(
        run,
        key="document_parsing_completed",
        body=format_prompt_parse_summary(
            summary,
            project_name=project.name,
            skipped_count=int(import_data.get("skipped_count") or 0),
        ),
    )
    run.status = "queued"
    run.worker_lease_until = None
    await session.commit()
    return await _advance_plan(session, run=run)


async def _advance_review_wait(
    session: AsyncSession,
    *,
    run: PromptRun,
) -> PromptAdvanceResult:
    if run.review_job_id is None:
        raise PromptExecutionError(
            "review_job_binding_missing",
            "Gjenerimi nuk është lidhur me kërkesën /prompt.",
        )
    job = await reviews_service.get_review_job(
        session,
        job_id=run.review_job_id,
        user_id=run.user_id,
    )
    if job.project_id != run.project_id:
        raise PromptExecutionError(
            "review_job_project_mismatch",
            "Gjenerimi nuk i përket projektit të kësaj kërkese.",
        )
    if job.status == "failed":
        raise PromptExecutionError(
            "review_job_failed",
            "Gjenerimi i Akt-Kolaudimit dështoi.\n\n"
            f"{display_review_job_error(job.error_message)}",
        )
    if job.status == "waiting_for_quota":
        prompting_service.queue_prompt_notification(
            run,
            key=f"review_quota_{job.retry_count}",
            body=(
                "Gjenerimi u ndal përkohësisht nga kufiri i provider-it AI.\n"
                f"Riprovohet automatikisht: {display_retry_time(job.retry_after_at)}"
            ),
        )
        await prompting_service.release_prompt_run_lease(session, run=run)
        return PromptAdvanceResult(
            reschedule_after_seconds=_review_poll_delay(job.retry_after_at),
        )
    if job.status != "completed":
        await prompting_service.release_prompt_run_lease(session, run=run)
        return PromptAdvanceResult(
            reschedule_after_seconds=settings.prompt_review_poll_seconds,
        )

    run.status = "queued"
    run.worker_lease_until = None
    await session.commit()
    return await _advance_plan(session, run=run)


async def _ensure_dependencies_completed(
    session: AsyncSession,
    *,
    run: PromptRun,
    action: PromptAction,
) -> None:
    for dependency in action.depends_on:
        step = await prompting_service.get_prompt_step(
            session,
            run_id=run.id,
            step_key=dependency,
        )
        if step is None or step.status != "completed":
            raise PromptExecutionError(
                "prompt_dependency_incomplete",
                f"Hapi {action.id} nuk mund të nisë para {dependency}.",
            )


async def _estimate_dependency_step(
    session: AsyncSession,
    *,
    run: PromptRun,
    action: PromptAction,
):
    for dependency in action.depends_on:
        step = await prompting_service.get_prompt_step(
            session,
            run_id=run.id,
            step_key=dependency,
        )
        if step is not None and step.action_type == "estimate_kolaudim":
            return step
    raise PromptExecutionError(
        "generation_estimate_missing",
        "Gjenerimi nuk ka një vlerësim paraprak të lidhur.",
    )


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


def _uuid_from_data(data: dict, key: str) -> UUID:
    try:
        return UUID(str(data[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise PromptExecutionError(
            "prompt_result_reference_invalid",
            f"Rezultati nuk përmban referencën {key}.",
        ) from exc


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


def _review_poll_delay(retry_after_at: datetime | None) -> int:
    if retry_after_at is None:
        return settings.prompt_review_poll_seconds
    if retry_after_at.tzinfo is None:
        retry_after_at = retry_after_at.replace(tzinfo=timezone.utc)
    remaining = max(1, int((retry_after_at - datetime.now(timezone.utc)).total_seconds()))
    return min(remaining + 2, 300)
