from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.files import service
from app.files.schemas import (
    FileVersionRead,
    ParsedDocumentRead,
    ProjectFileRead,
    ProjectFileUploadRead,
)
from app.users.dependencies import get_current_user
from app.users.models import User

router = APIRouter(prefix="/projects/{project_id}/files", tags=["files"])


@router.post("", response_model=ProjectFileUploadRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    project_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectFileUploadRead:
    project_file, file_version = await service.create_project_file(
        session,
        project_id=project_id,
        upload=file,
        user_id=current_user.id,
    )
    return ProjectFileUploadRead(
        file=ProjectFileRead.model_validate(project_file),
        version=FileVersionRead.model_validate(file_version),
    )


@router.get("", response_model=list[ProjectFileRead])
async def list_files(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectFileRead]:
    files = await service.list_project_files(
        session,
        project_id=project_id,
        user_id=current_user.id,
    )
    return [ProjectFileRead.model_validate(project_file) for project_file in files]


@router.get("/{file_id}", response_model=ProjectFileRead)
async def get_file(
    project_id: UUID,
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProjectFileRead:
    project_file = await service.get_project_file(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=current_user.id,
    )
    return ProjectFileRead.model_validate(project_file)


@router.post(
    "/{file_id}/versions",
    response_model=FileVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_file_version(
    project_id: UUID,
    file_id: UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileVersionRead:
    file_version = await service.create_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        upload=file,
        user_id=current_user.id,
    )
    return FileVersionRead.model_validate(file_version)


@router.get("/{file_id}/versions", response_model=list[FileVersionRead])
async def list_file_versions(
    project_id: UUID,
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FileVersionRead]:
    versions = await service.list_file_versions(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=current_user.id,
    )
    return [FileVersionRead.model_validate(version) for version in versions]


@router.get("/{file_id}/parsed", response_model=ParsedDocumentRead)
async def get_current_parsed_document(
    project_id: UUID,
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ParsedDocumentRead:
    parsed_document = await service.get_parsed_document_for_current_version(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=current_user.id,
    )
    return ParsedDocumentRead.model_validate(parsed_document)


@router.get("/{file_id}/versions/{version_id}/parsed", response_model=ParsedDocumentRead)
async def get_version_parsed_document(
    project_id: UUID,
    file_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ParsedDocumentRead:
    parsed_document = await service.get_parsed_document_for_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=current_user.id,
    )
    return ParsedDocumentRead.model_validate(parsed_document)


@router.post("/{file_id}/classify", response_model=ParsedDocumentRead)
async def classify_current_parsed_document(
    project_id: UUID,
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ParsedDocumentRead:
    parsed_document = await service.classify_parsed_document_for_current_version(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=current_user.id,
    )
    return ParsedDocumentRead.model_validate(parsed_document)


@router.post("/{file_id}/versions/{version_id}/classify", response_model=ParsedDocumentRead)
async def classify_version_parsed_document(
    project_id: UUID,
    file_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ParsedDocumentRead:
    parsed_document = await service.classify_parsed_document_for_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=current_user.id,
    )
    return ParsedDocumentRead.model_validate(parsed_document)
