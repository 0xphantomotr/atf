import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from fastapi import HTTPException, status
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.core.config import settings
from app.files import service as files_service
from app.files.storage import ensure_bucket_exists, get_minio_client
from app.projects.service import get_project
from app.prompting.models import PromptRun


class AsyncUpload(Protocol):
    filename: str
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


class PersistedPromptUpload:
    def __init__(self, *, filename: str, content_type: str, content: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._buffer = BytesIO(content)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


@dataclass(frozen=True)
class PromptAttachmentImportResult:
    project_id: UUID
    version_ids: tuple[UUID, ...]
    uploaded_count: int
    reused_count: int
    skipped: tuple[dict[str, str], ...]

    def as_step_data(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "file_version_ids": [str(version_id) for version_id in self.version_ids],
            "uploaded_count": self.uploaded_count,
            "reused_count": self.reused_count,
            "skipped_count": len(self.skipped),
            "skipped": list(self.skipped),
        }


async def persist_prompt_attachment(
    session: AsyncSession,
    *,
    run: PromptRun,
    upload: AsyncUpload,
    telegram_file_id: str,
    telegram_file_unique_id: str,
) -> dict[str, Any]:
    current = dict(run.attachment_metadata or {})
    if current.get("storage_path"):
        return current

    content = await upload.read()
    size_bytes = len(content)
    if size_bytes == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dokumenti i bashkëngjitur është bosh.",
        )
    if size_bytes > files_service.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Dokumenti është më i madh se kufiri i lejuar prej 50 MB.",
        )

    sha256_hash = hashlib.sha256(content).hexdigest()
    safe_filename = files_service.normalize_filename(upload.filename)
    storage_path = (
        f"prompt-runs/{run.id}/attachments/{sha256_hash[:16]}-{safe_filename}"
    )
    client = get_minio_client()
    try:
        ensure_bucket_exists(client, settings.minio_bucket)
        client.put_object(
            settings.minio_bucket,
            storage_path,
            BytesIO(content),
            length=size_bytes,
            content_type=upload.content_type or "application/octet-stream",
        )
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Attachment-i nuk u ruajt në object storage: {exc.code}",
        ) from exc

    metadata = {
        "filename": upload.filename,
        "content_type": upload.content_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "sha256_hash": sha256_hash,
        "storage_bucket": settings.minio_bucket,
        "storage_path": storage_path,
        "telegram_file_id": telegram_file_id,
        "telegram_file_unique_id": telegram_file_unique_id,
        "notifications": {},
    }
    run.attachment_metadata = metadata
    await write_audit_log(
        session,
        action="prompt.attachment.persisted",
        entity_type="prompt_run",
        entity_id=run.id,
        actor_user_id=run.user_id,
        project_id=run.project_id,
        details={
            "filename": upload.filename,
            "size_bytes": size_bytes,
            "sha256_hash": sha256_hash,
        },
    )
    await session.commit()
    return metadata


async def import_persisted_attachment(
    session: AsyncSession,
    *,
    run: PromptRun,
    project_id: UUID,
    user_id: UUID,
) -> PromptAttachmentImportResult:
    await get_project(session, project_id=project_id, user_id=user_id)
    metadata = dict(run.attachment_metadata or {})
    upload = _load_persisted_upload(metadata)
    suffix = Path(upload.filename).suffix.lower()

    if suffix == ".zip":
        uploaded, skipped = await files_service.import_project_files_zip(
            session,
            project_id=project_id,
            upload=upload,
            user_id=user_id,
            deduplicate=True,
        )
        uploaded_version_ids = [version.id for _, version in uploaded]
        reused_version_ids = [
            UUID(item["file_version_id"])
            for item in skipped
            if item.get("reason") == "already imported" and item.get("file_version_id")
        ]
        unsupported = tuple(
            item for item in skipped if item.get("reason") != "already imported"
        )
        version_ids = tuple(uploaded_version_ids + reused_version_ids)
        if not version_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Attachment-i nuk përmban dokumente të mbështetura për importim.",
            )
        return PromptAttachmentImportResult(
            project_id=project_id,
            version_ids=version_ids,
            uploaded_count=len(uploaded_version_ids),
            reused_count=len(reused_version_ids),
            skipped=unsupported,
        )

    sha256_hash = str(metadata.get("sha256_hash") or "")
    existing = await files_service.find_current_file_version_by_identity(
        session,
        project_id=project_id,
        original_filename=upload.filename,
        sha256_hash=sha256_hash,
    )
    if existing is not None:
        return PromptAttachmentImportResult(
            project_id=project_id,
            version_ids=(existing.id,),
            uploaded_count=0,
            reused_count=1,
            skipped=(),
        )

    _, version = await files_service.create_project_file(
        session,
        project_id=project_id,
        upload=upload,
        user_id=user_id,
    )
    return PromptAttachmentImportResult(
        project_id=project_id,
        version_ids=(version.id,),
        uploaded_count=1,
        reused_count=0,
        skipped=(),
    )


def _load_persisted_upload(metadata: dict[str, Any]) -> PersistedPromptUpload:
    bucket = str(metadata.get("storage_bucket") or "")
    storage_path = str(metadata.get("storage_path") or "")
    filename = str(metadata.get("filename") or "")
    if not bucket or not storage_path or not filename:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Attachment-i i ruajtur nuk është i plotë.",
        )

    client = get_minio_client()
    response = None
    try:
        response = client.get_object(bucket, storage_path)
        content = response.read()
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Attachment-i nuk u lexua nga object storage: {exc.code}",
        ) from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()

    return PersistedPromptUpload(
        filename=filename,
        content_type=str(metadata.get("content_type") or "application/octet-stream"),
        content=content,
    )
