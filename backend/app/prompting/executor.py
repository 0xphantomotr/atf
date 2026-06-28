from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.projects import service as projects_service
from app.projects.models import Project
from app.projects.schemas import ProjectCreate
from app.prompting import service as prompting_service
from app.prompting.models import PromptRun
from app.prompting.schemas import PromptActionResult, PromptPlan
from app.telegram.service import (
    display_review_job_stage,
    get_active_project,
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
        arguments = action.arguments.model_dump(mode="json")
        step, should_execute = await prompting_service.start_prompt_step(
            session,
            run=run,
            step_key=action.id,
            action_type=action.type,
            arguments=arguments,
        )
        if not should_execute:
            results.append(_result_from_step(step.result_data, action.id, action.type))
            continue

        try:
            action_result, project_id = await _execute_action(
                session,
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
            )
            results.append(action_result)
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
    return results


async def _execute_action(
    session: AsyncSession,
    *,
    action_type: str,
    arguments: dict,
    user_id: UUID,
) -> tuple[PromptActionResult, UUID | None]:
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
        )

    raise PromptExecutionError(
        "action_not_implemented",
        f"Veprimi {action_type} nuk është implementuar.",
    )


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


def _result_from_step(data: dict, step_key: str, action_type: str) -> PromptActionResult:
    normalized = dict(data)
    normalized["step_key"] = step_key
    normalized["action_type"] = action_type
    return PromptActionResult.model_validate(normalized)
