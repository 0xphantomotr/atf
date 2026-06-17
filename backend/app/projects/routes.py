from uuid import UUID

from fastapi import APIRouter

from app.core.errors import NotImplementedYet
from app.projects.schemas import ProjectCreate, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("")
async def create_project(_: ProjectCreate) -> None:
    raise NotImplementedYet()


@router.get("")
async def list_projects() -> None:
    raise NotImplementedYet()


@router.get("/{project_id}")
async def get_project(project_id: UUID) -> None:
    _ = project_id
    raise NotImplementedYet()


@router.patch("/{project_id}")
async def update_project(project_id: UUID, _: ProjectUpdate) -> None:
    _ = project_id
    raise NotImplementedYet()


@router.delete("/{project_id}")
async def delete_project(project_id: UUID) -> None:
    _ = project_id
    raise NotImplementedYet()

