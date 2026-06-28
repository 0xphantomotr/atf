from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.models import Project, ProjectMember
from app.projects.schemas import ProjectCreate, ProjectUpdate


async def create_project(
    session: AsyncSession,
    *,
    payload: ProjectCreate,
    user_id: UUID,
    commit: bool = True,
) -> Project:
    project = Project(
        name=payload.name,
        project_type=payload.project_type,
        stage=payload.stage,
        location=payload.location,
        description=payload.description,
        language="sq-AL",
        created_by=user_id,
    )
    session.add(project)
    await session.flush()

    session.add(ProjectMember(project_id=project.id, user_id=user_id, role="owner"))
    if commit:
        await session.commit()
        await session.refresh(project)
    return project


async def list_projects(session: AsyncSession, *, user_id: UUID) -> list[Project]:
    result = await session.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user_id,
            Project.deleted_at.is_(None),
        )
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().unique())


async def get_project(session: AsyncSession, *, project_id: UUID, user_id: UUID) -> Project:
    result = await session.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            and_(
                Project.id == project_id,
                ProjectMember.user_id == user_id,
                Project.deleted_at.is_(None),
            )
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Projekti nuk u gjet.",
        )
    return project


async def update_project(
    session: AsyncSession,
    *,
    project_id: UUID,
    payload: ProjectUpdate,
    user_id: UUID,
) -> Project:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)
    return project


async def delete_project(session: AsyncSession, *, project_id: UUID, user_id: UUID) -> None:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    project.deleted_at = datetime.now(timezone.utc)
    await session.commit()
