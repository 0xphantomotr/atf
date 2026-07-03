from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import service as ai_service
from app.google_drive.service import (
    GoogleDriveError,
    bind_google_drive_folder,
    google_drive_binding_status,
    import_google_drive_folder,
    preflight_google_drive_folder,
    sync_google_drive_folder,
    upload_generated_pdf_to_drive,
)
from app.projects import service as projects_service
from app.projects.models import Project
from app.projects.schemas import ProjectCreate
from app.prompting import service as prompting_service
from app.prompting.attachments import import_persisted_attachment
from app.prompting.context import load_qa_follow_up_context
from app.prompting.generation import (
    format_generation_estimate,
    generation_estimate_fingerprint,
    kolaudim_request,
)
from app.prompting.models import PromptRun
from app.prompting.qa import ProjectQuestionError, answer_project_question
from app.prompting.schemas import PromptAction, PromptActionResult, PromptPlan
from app.reviews import service as reviews_service
from app.telegram.service import (
    display_review_job_stage,
    get_active_project,
    get_latest_completed_review_job,
    get_latest_review_job,
    set_active_project,
)


class PromptExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


async def execute_prompt_plan(
    session: AsyncSession,
    *,
    run: PromptRun,
    plan: PromptPlan,
    user_id: UUID,
) -> list[PromptActionResult]:
    results: list[PromptActionResult] = []
    for action in plan.actions:
        results.append(
            await execute_prompt_action(
                session,
                run=run,
                action=action,
                user_id=user_id,
            )
        )
    return results


async def execute_prompt_action(
    session: AsyncSession,
    *,
    run: PromptRun,
    action: PromptAction,
    user_id: UUID,
) -> PromptActionResult:
    arguments = action.arguments.model_dump(mode="json")
    step, should_execute = await prompting_service.start_prompt_step(
        session,
        run=run,
        step_key=action.id,
        action_type=action.type,
        arguments=arguments,
    )
    if not should_execute:
        return _result_from_step(step.result_data, action.id, action.type)

    try:
        action_result, project_id, review_job_id = await _execute_action(
            session,
            run=run,
            action_type=action.type,
            arguments=arguments,
            user_id=user_id,
        )
        action_result = action_result.model_copy(update={"step_key": action.id})
        await prompting_service.complete_prompt_step(
            session,
            run=run,
            step=step,
            result=action_result.model_dump(mode="json"),
            project_id=project_id,
            review_job_id=review_job_id,
        )
        return action_result
    except PromptExecutionError as exc:
        await prompting_service.fail_prompt_step(
            session,
            step_id=step.id,
            code=exc.code,
            detail=exc.user_message,
        )
        raise
    except Exception as exc:
        await prompting_service.fail_prompt_step(
            session,
            step_id=step.id,
            code="action_failed",
            detail=str(exc),
        )
        raise


async def _execute_action(
    session: AsyncSession,
    *,
    run: PromptRun,
    action_type: str,
    arguments: dict,
    user_id: UUID,
) -> tuple[PromptActionResult, UUID | None, UUID | None]:
    if action_type == "list_projects":
        projects = await projects_service.list_projects(session, user_id=user_id)
        active = await get_active_project(session, user_id=user_id)
        message = _projects_message(projects, active_project_id=active.id if active else None)
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=message,
                data={
                    "count": len(projects),
                    "project_ids": [str(project.id) for project in projects],
                },
            ),
            active.id if active else None,
            None,
        )

    if action_type == "create_project":
        project_name = _require_project_name(arguments)
        project = await projects_service.create_project(
            session,
            payload=ProjectCreate(
                name=project_name,
                project_type="residential",
                stage="during_construction",
                location=None,
                description=None,
            ),
            user_id=user_id,
            commit=False,
        )
        await set_active_project(
            session,
            user_id=user_id,
            project_id=project.id,
            commit=False,
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=f"Projekti u krijua dhe u zgjodh si aktiv: {project.name}",
                data={"project_id": str(project.id), "project_name": project.name},
            ),
            project.id,
            None,
        )

    if action_type == "select_project":
        project_name = _require_project_name(arguments)
        project = await _resolve_project_by_name(
            session,
            user_id=user_id,
            name=project_name,
        )
        await set_active_project(
            session,
            user_id=user_id,
            project_id=project.id,
            commit=False,
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=f"Projekti aktiv u zgjodh: {project.name}",
                data={"project_id": str(project.id), "project_name": project.name},
            ),
            project.id,
            None,
        )

    if action_type == "show_active_project":
        project = await get_active_project(session, user_id=user_id)
        if project is None:
            raise PromptExecutionError(
                "active_project_missing",
                "Nuk keni projekt aktiv. Kërkoni krijimin ose zgjedhjen e një projekti.",
            )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    f"Projekti aktiv: {project.name}\n"
                    f"Tipi: {project.project_type}\n"
                    f"Faza: {project.stage}"
                ),
                data={"project_id": str(project.id), "project_name": project.name},
            ),
            project.id,
            None,
        )

    if action_type == "get_status":
        project = await get_active_project(session, user_id=user_id)
        if project is None:
            raise PromptExecutionError(
                "active_project_missing",
                "Nuk keni projekt aktiv për të kontrolluar statusin.",
            )
        job = await get_latest_review_job(
            session,
            user_id=user_id,
            project_id=project.id,
        )
        if job is None:
            message = f"Projekti {project.name} nuk ka ende gjenerime."
            data = {"project_id": str(project.id), "status": "not_started"}
        else:
            stage = display_review_job_stage(job.current_stage)
            message = (
                f"Projekti: {project.name}\n"
                f"Statusi: {job.status}\n"
                f"Progresi: {job.progress}%"
            )
            if stage:
                message = f"{message}\nFaza: {stage}"
            data = {
                "project_id": str(project.id),
                "review_job_id": str(job.id),
                "status": job.status,
                "progress": job.progress,
            }
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=message,
                data=data,
            ),
            project.id,
            None,
        )

    if action_type == "show_drive_folder":
        project = await _prompt_project(session, run=run, user_id=user_id)
        binding = await google_drive_binding_status(
            session,
            project_id=project.id,
            user_id=user_id,
        )
        if binding is None:
            raise PromptExecutionError(
                "drive_folder_not_bound",
                "Projekti aktiv nuk ka folder Google Drive të lidhur.",
            )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=_drive_binding_message(binding),
                data={
                    "project_id": str(project.id),
                    "drive_folder_id": binding.folder_id,
                    "drive_folder_name": binding.folder_name,
                    "drive_folder_url": binding.folder_url,
                    "sync_status": binding.sync_status,
                    "last_sync_summary": binding.last_sync_summary,
                },
            ),
            project.id,
            None,
        )

    if action_type == "bind_drive_folder":
        project = await _prompt_project(session, run=run, user_id=user_id)
        try:
            binding = await bind_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user_id,
                folder_url=_require_drive_url(arguments),
            )
        except GoogleDriveError as exc:
            raise PromptExecutionError("drive_folder_bind_failed", str(exc)) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    f"Folderi Google Drive u lidh me projektin {project.name}: "
                    f"{binding.folder_name}"
                ),
                data={
                    "project_id": str(project.id),
                    "drive_folder_id": binding.folder_id,
                    "drive_folder_name": binding.folder_name,
                    "drive_folder_url": binding.folder_url,
                },
            ),
            project.id,
            None,
        )

    if action_type == "check_drive_folder":
        project = await _prompt_project(session, run=run, user_id=user_id)
        try:
            preflight = await preflight_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user_id,
                folder_url=arguments.get("drive_url"),
            )
        except GoogleDriveError as exc:
            raise PromptExecutionError("drive_preflight_failed", str(exc)) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=_drive_preflight_message(preflight),
                data={
                    "project_id": str(project.id),
                    "drive_folder_id": preflight.folder_id,
                    "drive_folder_name": preflight.folder_name,
                    "readable": preflight.readable,
                    "writable": preflight.writable,
                    "new_count": preflight.new_count,
                    "changed_count": preflight.changed_count,
                    "unchanged_count": preflight.unchanged_count,
                    "deleted_count": preflight.deleted_count,
                    "skipped_count": preflight.skipped_count,
                },
            ),
            project.id,
            None,
        )

    if action_type == "sync_drive_folder":
        project = await _prompt_project(session, run=run, user_id=user_id)
        try:
            sync_result = await sync_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user_id,
            )
        except GoogleDriveError as exc:
            raise PromptExecutionError("drive_folder_sync_failed", str(exc)) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=_drive_sync_message(sync_result),
                data=sync_result.as_step_data(),
            ),
            project.id,
            None,
        )

    if action_type == "import_attachment":
        project = await get_active_project(session, user_id=user_id)
        if project is None:
            raise PromptExecutionError(
                "active_project_missing",
                "Zgjidhni ose krijoni një projekt para importimit të attachment-it.",
            )
        try:
            import_result = await import_persisted_attachment(
                session,
                run=run,
                project_id=project.id,
                user_id=user_id,
            )
        except HTTPException as exc:
            if exc.status_code >= 500:
                raise
            raise PromptExecutionError(
                "attachment_import_failed",
                str(exc.detail),
            ) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    "Attachment-i u importua për përpunim.\n"
                    f"Dokumente të reja: {import_result.uploaded_count}\n"
                    f"Dokumente ekzistuese të ripërdorura: {import_result.reused_count}\n"
                    f"Të anashkaluara: {len(import_result.skipped)}"
                ),
                data=import_result.as_step_data(),
            ),
            project.id,
            None,
        )

    if action_type == "import_drive_folder":
        project = await get_active_project(session, user_id=user_id)
        if project is None:
            raise PromptExecutionError(
                "active_project_missing",
                "Zgjidhni ose krijoni një projekt para importimit nga Google Drive.",
            )
        try:
            import_result = await import_google_drive_folder(
                session,
                project_id=project.id,
                user_id=user_id,
                folder_url=_require_drive_url(arguments),
            )
        except GoogleDriveError as exc:
            raise PromptExecutionError("drive_folder_import_failed", str(exc)) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    f"Folderi Google Drive u importua: {import_result.folder_name}\n"
                    f"Dokumente të reja: {import_result.uploaded_count}\n"
                    f"Dokumente të ndryshuara: {import_result.changed_count}\n"
                    f"Dokumente të pandryshuara: {import_result.reused_count}\n"
                    f"Dokumente të hequra nga burimi: {import_result.deleted_count}\n"
                    f"Të anashkaluara: {len(import_result.skipped)}"
                ),
                data=import_result.as_step_data(),
            ),
            project.id,
            None,
        )

    if action_type == "estimate_kolaudim":
        project = await _prompt_project(session, run=run, user_id=user_id)
        estimate = await reviews_service.estimate_review_job(
            session,
            project_id=project.id,
            user_id=user_id,
            payload=kolaudim_request(),
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=format_generation_estimate(project.name, estimate),
                data={
                    "project_id": str(project.id),
                    "estimate": estimate,
                    "estimate_fingerprint": generation_estimate_fingerprint(estimate),
                },
            ),
            project.id,
            None,
        )

    if action_type == "generate_kolaudim":
        if run.confirmed_at is None:
            raise PromptExecutionError(
                "generation_not_confirmed",
                "Gjenerimi nuk është konfirmuar.",
            )
        project = await _prompt_project(session, run=run, user_id=user_id)
        job = await reviews_service.create_review_job(
            session,
            project_id=project.id,
            user_id=user_id,
            payload=kolaudim_request(),
            prompt_run_id=run.id,
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=f"Gjenerimi i Akt-Kolaudimit nisi për projektin: {project.name}",
                data={
                    "project_id": str(project.id),
                    "review_job_id": str(job.id),
                    "status": job.status,
                },
            ),
            project.id,
            job.id,
        )

    if action_type == "deliver_latest_report":
        project = await _prompt_project(session, run=run, user_id=user_id)
        job, pdf_output = await _resolve_report_output(
            session,
            run=run,
            project_id=project.id,
            user_id=user_id,
            allow_latest=arguments.get("job_ref") is None,
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=f"Akt-Kolaudimi është gati për projektin: {project.name}",
                data={
                    "project_id": str(project.id),
                    "review_job_id": str(job.id),
                    "output_id": str(pdf_output.id),
                    "project_name": project.name,
                },
            ),
            project.id,
            job.id,
        )

    if action_type == "upload_report_to_drive":
        project = await _prompt_project(session, run=run, user_id=user_id)
        job, pdf_output = await _resolve_report_output(
            session,
            run=run,
            project_id=project.id,
            user_id=user_id,
            allow_latest=arguments.get("job_ref") is None,
        )
        try:
            uploaded = await upload_generated_pdf_to_drive(
                session,
                output_id=pdf_output.id,
                expected_review_job_id=job.id,
                prompt_run_id=run.id,
                user_id=user_id,
                folder_url=arguments.get("drive_url"),
            )
        except GoogleDriveError as exc:
            raise PromptExecutionError("drive_report_upload_failed", str(exc)) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    "Akt-Kolaudimi u ruajt në Google Drive.\n"
                    f"Skedari: {uploaded.filename}\n"
                    f"Linku: {uploaded.web_view_link}"
                ),
                data={
                    "project_id": str(project.id),
                    "review_job_id": str(job.id),
                    "output_id": str(pdf_output.id),
                    "drive_file_id": uploaded.file_id,
                    "drive_web_view_link": uploaded.web_view_link,
                    "drive_output_folder_id": uploaded.output_folder_id,
                    "drive_output_folder_name": uploaded.output_folder_name,
                    "reused": uploaded.reused,
                },
            ),
            project.id,
            job.id,
        )

    if action_type == "select_ai_model":
        model = arguments.get("model")
        if not isinstance(model, str) or not model.strip():
            raise PromptExecutionError(
                "ai_model_missing",
                "Kërkesa nuk përmban emrin e modelit AI.",
            )
        setting = await ai_service.update_user_ai_model(
            session,
            user_id=user_id,
            model=model,
        )
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=(
                    "Modeli AI u përditësua.\n"
                    f"Provider: {setting.provider}\n"
                    f"Modeli: {setting.selected_model}"
                ),
                data={
                    "provider": setting.provider,
                    "model": setting.selected_model,
                },
            ),
            run.project_id,
            None,
        )

    if action_type == "answer_project_question":
        project = await _prompt_project(session, run=run, user_id=user_id)
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            raise PromptExecutionError(
                "project_question_missing",
                "Kërkesa nuk përmban pyetjen për dosjen teknike.",
            )
        ai_settings = await ai_service.get_user_ai_credentials(
            session,
            user_id=user_id,
        )
        if ai_settings is None:
            raise PromptExecutionError(
                "ai_settings_missing",
                "Pyetja për dosjen kërkon konfigurim AI. Përdorni /ai_key.",
            )
        try:
            follow_up_context = await load_qa_follow_up_context(
                session,
                user_id=user_id,
                telegram_chat_id=run.telegram_chat_id,
                project_id=project.id,
                exclude_run_id=run.id,
            )
            answer = await answer_project_question(
                session,
                project_id=project.id,
                question=question,
                ai_settings=ai_settings,
                follow_up_context=follow_up_context,
            )
        except ProjectQuestionError as exc:
            raise PromptExecutionError(
                "project_question_failed",
                str(exc),
            ) from exc
        return (
            PromptActionResult(
                step_key="",
                action_type=action_type,
                message=answer.message,
                data={
                    "project_id": str(project.id),
                    "project_name": project.name,
                    "answer": answer.answer,
                    "certainty": answer.certainty,
                    "evidence_ids": answer.evidence_ids,
                    "source_labels": answer.source_labels,
                    "follow_up_suggestion": answer.follow_up_suggestion,
                    "token_usage": answer.token_usage,
                    "retrieval": answer.retrieval,
                },
            ),
            project.id,
            None,
        )

    raise PromptExecutionError(
        "action_not_implemented",
        f"Veprimi {action_type} nuk është implementuar.",
    )


async def _resolve_report_output(
    session: AsyncSession,
    *,
    run: PromptRun,
    project_id: UUID,
    user_id: UUID,
    allow_latest: bool,
):
    job = None
    if run.review_job_id is not None:
        job = await reviews_service.get_review_job(
            session,
            job_id=run.review_job_id,
            user_id=user_id,
        )
    elif allow_latest:
        job = await get_latest_completed_review_job(
            session,
            user_id=user_id,
            project_id=project_id,
        )
    if job is None or job.project_id != project_id:
        raise PromptExecutionError(
            "report_job_missing",
            "Nuk u gjet një gjenerim i përfunduar për këtë kërkesë.",
        )
    if job.status != "completed":
        raise PromptExecutionError(
            "report_not_ready",
            f"Akt-Kolaudimi nuk është ende gati. Statusi: {job.status}.",
        )
    _, outputs = await reviews_service.get_review_job_outputs(
        session,
        job_id=job.id,
        user_id=user_id,
    )
    pdf_output = next((item for item in outputs if item.output_type == "pdf"), None)
    if pdf_output is None or pdf_output.review_job_id != job.id:
        raise PromptExecutionError(
            "report_pdf_missing",
            "Gjenerimi përfundoi, por PDF-ja e lidhur me të nuk u gjet.",
        )
    return job, pdf_output


def _require_drive_url(arguments: dict) -> str:
    value = arguments.get("drive_url")
    if not isinstance(value, str) or not value.strip():
        raise PromptExecutionError(
            "drive_folder_url_missing",
            "Kërkesa nuk përmban link të vlefshëm të folderit Google Drive.",
        )
    return value.strip()


async def _prompt_project(
    session: AsyncSession,
    *,
    run: PromptRun,
    user_id: UUID,
) -> Project:
    if run.project_id is not None:
        return await projects_service.get_project(
            session,
            project_id=run.project_id,
            user_id=user_id,
        )
    project = await get_active_project(session, user_id=user_id)
    if project is None:
        raise PromptExecutionError(
            "active_project_missing",
            "Nuk keni projekt aktiv për këtë veprim.",
        )
    return project


async def _resolve_project_by_name(
    session: AsyncSession,
    *,
    user_id: UUID,
    name: str,
) -> Project:
    projects = await projects_service.list_projects(session, user_id=user_id)
    normalized_name = _normalize_project_name(name)
    matches = [
        project
        for project in projects
        if _normalize_project_name(project.name) == normalized_name
    ]
    if not matches:
        raise PromptExecutionError(
            "project_not_found",
            f"Nuk u gjet projekti me emrin: {name}",
        )
    if len(matches) > 1:
        raise PromptExecutionError(
            "project_ambiguous",
            f"Ka më shumë se një projekt me emrin {name}. Përdorni /projektet.",
        )
    return matches[0]


def _normalize_project_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _require_project_name(arguments: dict) -> str:
    value = arguments.get("name")
    if not isinstance(value, str) or not value.strip():
        raise PromptExecutionError(
            "project_name_missing",
            "Kërkesa nuk përmban emrin e projektit.",
        )
    return value


def _projects_message(projects: list[Project], *, active_project_id: UUID | None) -> str:
    if not projects:
        return "Nuk keni ende projekte."
    lines = ["Projektet tuaja:"]
    for index, project in enumerate(projects, start=1):
        suffix = " (aktiv)" if project.id == active_project_id else ""
        lines.append(f"{index}. {project.name}{suffix}")
    return "\n".join(lines)


def _drive_binding_message(binding) -> str:
    summary = dict(binding.last_sync_summary or {})
    lines = [
        f"Projekti: {binding.project_name}",
        f"Folderi Drive: {binding.folder_name}",
        f"Linku: {binding.folder_url}",
        f"Statusi i sinkronizimit: {binding.sync_status}",
    ]
    if binding.last_sync_completed_at is not None:
        lines.append(f"Sinkronizimi i fundit: {binding.last_sync_completed_at.isoformat()}")
    if summary:
        lines.append(
            "Ndryshimet e fundit: "
            f"{summary.get('new_count', 0)} të reja, "
            f"{summary.get('changed_count', 0)} të ndryshuara, "
            f"{summary.get('deleted_count', 0)} të hequra"
        )
    return "\n".join(lines)


def _drive_preflight_message(preflight) -> str:
    return (
        "Kontrolli i Google Drive\n\n"
        f"Folderi: {preflight.folder_name}\n"
        f"Lexim: {'Po' if preflight.readable else 'Jo'}\n"
        f"Shkrim: {'Po' if preflight.writable else 'Jo'}\n"
        f"Dokumente të mbështetura: {preflight.supported_count}\n"
        f"Të reja: {preflight.new_count}\n"
        f"Të ndryshuara: {preflight.changed_count}\n"
        f"Të pandryshuara: {preflight.unchanged_count}\n"
        f"Të hequra nga Drive: {preflight.deleted_count}\n"
        f"Të anashkaluara: {preflight.skipped_count}"
    )


def _drive_sync_message(result) -> str:
    return (
        f"Google Drive u sinkronizua: {result.folder_name}\n"
        f"Dokumente të reja: {result.uploaded_count}\n"
        f"Dokumente të ndryshuara: {result.changed_count}\n"
        f"Dokumente të pandryshuara: {result.reused_count}\n"
        f"Dokumente të hequra nga burimi: {result.deleted_count}\n"
        f"Të anashkaluara: {len(result.skipped)}"
    )


def _result_from_step(data: dict, step_key: str, action_type: str) -> PromptActionResult:
    normalized = dict(data)
    normalized["step_key"] = step_key
    normalized["action_type"] = action_type
    return PromptActionResult.model_validate(normalized)
