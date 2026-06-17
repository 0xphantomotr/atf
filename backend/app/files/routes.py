from uuid import UUID

from fastapi import APIRouter, UploadFile

from app.core.errors import NotImplementedYet

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])


@router.post("")
async def upload_file(project_id: UUID, file: UploadFile) -> None:
    _ = project_id, file
    raise NotImplementedYet()


@router.get("")
async def list_files(project_id: UUID) -> None:
    _ = project_id
    raise NotImplementedYet()


@router.get("/{file_id}")
async def get_file(project_id: UUID, file_id: UUID) -> None:
    _ = project_id, file_id
    raise NotImplementedYet()


@router.post("/{file_id}/versions")
async def create_file_version(project_id: UUID, file_id: UUID, file: UploadFile) -> None:
    _ = project_id, file_id, file
    raise NotImplementedYet()


@router.get("/{file_id}/versions")
async def list_file_versions(project_id: UUID, file_id: UUID) -> None:
    _ = project_id, file_id
    raise NotImplementedYet()

