import hashlib
import re
import unicodedata
import uuid
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.files.models import FileVersion, ProjectFile
from app.files.parser import is_supported_filename
from app.files.storage import ensure_bucket_exists, get_minio_client
from app.projects.service import get_project
from app.workers.jobs import parse_file_version

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SPOOL_MAX_BYTES = 10 * 1024 * 1024


def normalize_filename(filename: str) -> str:
    raw_name = Path(filename).name.strip()
    normalized = unicodedata.normalize("NFKD", raw_name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-._")

    suffix = Path(raw_name).suffix.lower()
    if not normalized:
        normalized = f"dokument{suffix}"
    if suffix and not normalized.endswith(suffix):
        normalized = f"{normalized}{suffix}"
    return normalized


async def _copy_upload_to_spooled_file(upload: UploadFile) -> tuple[BinaryIO, int, str]:
    sha256 = hashlib.sha256()
    size_bytes = 0
    spooled = SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES, mode="w+b")

    while chunk := await upload.read(1024 * 1024):
        size_bytes += len(chunk)
        if size_bytes > MAX_UPLOAD_BYTES:
            spooled.close()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Dokumenti është më i madh se kufiri i lejuar prej 50 MB.",
            )
        sha256.update(chunk)
        spooled.write(chunk)

    if size_bytes == 0:
        spooled.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dokumenti është bosh.",
        )

    spooled.seek(0)
    return spooled, size_bytes, sha256.hexdigest()


def _upload_to_minio(*, object_name: str, data: BinaryIO, size_bytes: int, content_type: str) -> None:
    client = get_minio_client()
    try:
        ensure_bucket_exists(client, settings.minio_bucket)
        client.put_object(
            settings.minio_bucket,
            object_name,
            data,
            length=size_bytes,
            content_type=content_type,
        )
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Nuk arrita ta ruaj dokumentin në objekt storage: {exc.code}",
        ) from exc


async def create_project_file(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    upload: UploadFile,
    user_id: uuid.UUID,
) -> tuple[ProjectFile, FileVersion]:
    await get_project(session, project_id=project_id, user_id=user_id)

    if not upload.filename or not is_supported_filename(upload.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formati nuk mbështetet. Ngarkoni PDF, DOCX, XLSX, ZIP, JPG ose PNG.",
        )

    normalized_filename = normalize_filename(upload.filename)
    content_type = upload.content_type or "application/octet-stream"
    file_id = uuid.uuid4()
    version_id = uuid.uuid4()
    storage_path = (
        f"projects/{project_id}/files/{file_id}/versions/1/{version_id}-{normalized_filename}"
    )

    data, size_bytes, sha256_hash = await _copy_upload_to_spooled_file(upload)
    try:
        _upload_to_minio(
            object_name=storage_path,
            data=data,
            size_bytes=size_bytes,
            content_type=content_type,
        )
    finally:
        data.close()

    project_file = ProjectFile(
        id=file_id,
        project_id=project_id,
        original_filename=upload.filename,
        normalized_filename=normalized_filename,
        mime_type=content_type,
        current_version=1,
        storage_bucket=settings.minio_bucket,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        uploaded_by=user_id,
    )
    file_version = FileVersion(
        id=version_id,
        file_id=file_id,
        version_number=1,
        original_filename=upload.filename,
        storage_bucket=settings.minio_bucket,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        mime_type=content_type,
        size_bytes=size_bytes,
        parse_status="pending",
        created_by=user_id,
    )

    session.add(project_file)
    await session.flush()
    session.add(file_version)
    await session.commit()
    await session.refresh(project_file)
    await session.refresh(file_version)
    parse_file_version.send(str(file_version.id))
    return project_file, file_version


async def list_project_files(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[ProjectFile]:
    await get_project(session, project_id=project_id, user_id=user_id)
    result = await session.execute(
        select(ProjectFile)
        .where(ProjectFile.project_id == project_id, ProjectFile.deleted_at.is_(None))
        .order_by(ProjectFile.created_at.desc())
    )
    return list(result.scalars())


async def get_project_file(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ProjectFile:
    await get_project(session, project_id=project_id, user_id=user_id)
    result = await session.execute(
        select(ProjectFile).where(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project_id,
            ProjectFile.deleted_at.is_(None),
        )
    )
    project_file = result.scalar_one_or_none()
    if project_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dokumenti nuk u gjet.",
        )
    return project_file


async def create_file_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    upload: UploadFile,
    user_id: uuid.UUID,
) -> FileVersion:
    project_file = await get_project_file(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=user_id,
    )

    if not upload.filename or not is_supported_filename(upload.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formati nuk mbështetet. Ngarkoni PDF, DOCX, XLSX, ZIP, JPG ose PNG.",
        )

    version_number = project_file.current_version + 1
    version_id = uuid.uuid4()
    normalized_filename = normalize_filename(upload.filename)
    content_type = upload.content_type or "application/octet-stream"
    storage_path = (
        f"projects/{project_id}/files/{file_id}/versions/"
        f"{version_number}/{version_id}-{normalized_filename}"
    )

    data, size_bytes, sha256_hash = await _copy_upload_to_spooled_file(upload)
    try:
        _upload_to_minio(
            object_name=storage_path,
            data=data,
            size_bytes=size_bytes,
            content_type=content_type,
        )
    finally:
        data.close()

    file_version = FileVersion(
        id=version_id,
        file_id=file_id,
        version_number=version_number,
        original_filename=upload.filename,
        storage_bucket=settings.minio_bucket,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
        mime_type=content_type,
        size_bytes=size_bytes,
        parse_status="pending",
        created_by=user_id,
    )
    project_file.original_filename = upload.filename
    project_file.normalized_filename = normalized_filename
    project_file.mime_type = content_type
    project_file.current_version = version_number
    project_file.storage_bucket = settings.minio_bucket
    project_file.storage_path = storage_path
    project_file.sha256_hash = sha256_hash

    session.add(file_version)
    await session.commit()
    await session.refresh(file_version)
    parse_file_version.send(str(file_version.id))
    return file_version


async def list_file_versions(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[FileVersion]:
    await get_project_file(session, project_id=project_id, file_id=file_id, user_id=user_id)
    result = await session.execute(
        select(FileVersion)
        .where(FileVersion.file_id == file_id)
        .order_by(FileVersion.version_number.desc())
    )
    return list(result.scalars())
