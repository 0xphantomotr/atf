import hashlib
import mimetypes
import re
import unicodedata
import uuid
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status
from minio.error import S3Error
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.files.classifier import UNKNOWN_DOCUMENT_TYPE, classify_document
from app.files.models import DocumentChunk, FileVersion, ParsedDocument, ProjectFile
from app.files.parser import is_supported_filename
from app.files.status import PARSED_STATUSES, is_parsed_status
from app.files.storage import ensure_bucket_exists, get_minio_client
from app.projects.service import get_project
from app.workers.jobs import parse_file_version

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
SPOOL_MAX_BYTES = 10 * 1024 * 1024
MAX_BULK_FILES = 250
MAX_ZIP_EXTRACTED_BYTES = 300 * 1024 * 1024
ZIP_IMPORT_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".mpp", ".jpg", ".jpeg", ".png"}


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


def _upload_to_minio(
    *,
    object_name: str,
    data: BinaryIO,
    size_bytes: int,
    content_type: str,
) -> None:
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


def _content_type_for_filename(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _is_supported_zip_member(filename: str) -> bool:
    return Path(filename).suffix.lower() in ZIP_IMPORT_EXTENSIONS


def _unsupported_file_reason(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".doc":
        return "format Word .doc nuk lexohet ende; konvertojeni në .docx"
    if suffix == ".mpp":
        return "format Microsoft Project .mpp nuk mund të lexohej"
    return "format i pambështetur"


def _safe_zip_member_filename(filename: str) -> str:
    normalized_name = filename.replace("\\", "/")
    parts = [
        part
        for part in PurePosixPath(normalized_name).parts
        if part not in {"", ".", "..", "/"}
    ]
    if not parts:
        return ""
    return "/".join(parts)


async def _create_project_file_from_data(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    original_filename: str,
    content_type: str,
    data: BinaryIO,
    size_bytes: int,
    sha256_hash: str,
    user_id: uuid.UUID,
) -> tuple[ProjectFile, FileVersion]:
    normalized_filename = normalize_filename(original_filename)
    file_id = uuid.uuid4()
    version_id = uuid.uuid4()
    storage_path = (
        f"projects/{project_id}/files/{file_id}/versions/1/{version_id}-{normalized_filename}"
    )

    data.seek(0)
    _upload_to_minio(
        object_name=storage_path,
        data=data,
        size_bytes=size_bytes,
        content_type=content_type,
    )

    project_file = ProjectFile(
        id=file_id,
        project_id=project_id,
        original_filename=original_filename,
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
        original_filename=original_filename,
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
            detail="Formati nuk mbështetet. Ngarkoni PDF, DOCX, XLSX, MPP, ZIP, JPG ose PNG.",
        )

    content_type = upload.content_type or "application/octet-stream"

    data, size_bytes, sha256_hash = await _copy_upload_to_spooled_file(upload)
    try:
        return await _create_project_file_from_data(
            session,
            project_id=project_id,
            original_filename=upload.filename,
            content_type=content_type,
            data=data,
            size_bytes=size_bytes,
            sha256_hash=sha256_hash,
            user_id=user_id,
        )
    finally:
        data.close()


async def create_project_files_bulk(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    uploads: list[UploadFile],
    user_id: uuid.UUID,
) -> tuple[list[tuple[ProjectFile, FileVersion]], list[dict[str, str]]]:
    await get_project(session, project_id=project_id, user_id=user_id)

    if len(uploads) > MAX_BULK_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Mund të ngarkohen maksimumi {MAX_BULK_FILES} dokumente njëherësh.",
        )

    uploaded: list[tuple[ProjectFile, FileVersion]] = []
    skipped: list[dict[str, str]] = []
    for upload in uploads:
        filename = upload.filename or "pa-emër"
        if not upload.filename or not is_supported_filename(upload.filename):
            skipped.append({"filename": filename, "reason": _unsupported_file_reason(filename)})
            continue

        data, size_bytes, sha256_hash = await _copy_upload_to_spooled_file(upload)
        try:
            uploaded.append(
                await _create_project_file_from_data(
                    session,
                    project_id=project_id,
                    original_filename=upload.filename,
                    content_type=upload.content_type or _content_type_for_filename(upload.filename),
                    data=data,
                    size_bytes=size_bytes,
                    sha256_hash=sha256_hash,
                    user_id=user_id,
                )
            )
        finally:
            data.close()

    return uploaded, skipped


async def import_project_files_zip(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    upload: UploadFile,
    user_id: uuid.UUID,
    deduplicate: bool = False,
) -> tuple[list[tuple[ProjectFile, FileVersion]], list[dict[str, str]]]:
    await get_project(session, project_id=project_id, user_id=user_id)

    if not upload.filename or Path(upload.filename).suffix.lower() != ".zip":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ngarkoni një arkiv ZIP të eksportuar nga dosja teknike.",
        )

    zip_data, _, _ = await _copy_upload_to_spooled_file(upload)
    uploaded: list[tuple[ProjectFile, FileVersion]] = []
    skipped: list[dict[str, str]] = []
    extracted_bytes = 0

    try:
        try:
            with zipfile.ZipFile(zip_data) as archive:
                file_infos = [info for info in archive.infolist() if not info.is_dir()]
                if len(file_infos) > MAX_BULK_FILES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"ZIP përmban më shumë se {MAX_BULK_FILES} dokumente.",
                    )

                for info in file_infos:
                    safe_filename = _safe_zip_member_filename(info.filename)
                    if not safe_filename:
                        skipped.append({"filename": info.filename, "reason": "emër i pavlefshëm"})
                        continue
                    if not _is_supported_zip_member(safe_filename):
                        skipped.append(
                            {
                                "filename": safe_filename,
                                "reason": _unsupported_file_reason(safe_filename),
                            }
                        )
                        continue
                    if info.file_size > MAX_UPLOAD_BYTES:
                        skipped.append(
                            {"filename": safe_filename, "reason": "dokument më i madh se 50 MB"}
                        )
                        continue

                    extracted_bytes += info.file_size
                    if extracted_bytes > MAX_ZIP_EXTRACTED_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="ZIP tejkalon kufirin total të importimit prej 300 MB.",
                        )

                    data, size_bytes, sha256_hash = _copy_zip_member_to_spooled_file(
                        archive,
                        info,
                    )
                    try:
                        if deduplicate:
                            existing = await find_current_file_version_by_identity(
                                session,
                                project_id=project_id,
                                original_filename=safe_filename,
                                sha256_hash=sha256_hash,
                            )
                            if existing is not None:
                                skipped.append(
                                    {
                                        "filename": safe_filename,
                                        "reason": "already imported",
                                        "file_version_id": str(existing.id),
                                    }
                                )
                                continue
                        uploaded.append(
                            await _create_project_file_from_data(
                                session,
                                project_id=project_id,
                                original_filename=safe_filename,
                                content_type=_content_type_for_filename(safe_filename),
                                data=data,
                                size_bytes=size_bytes,
                                sha256_hash=sha256_hash,
                                user_id=user_id,
                            )
                        )
                    finally:
                        data.close()
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Arkivi ZIP nuk mund të lexohet.",
            ) from exc
    finally:
        zip_data.close()

    return uploaded, skipped


async def find_current_file_version_by_identity(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    original_filename: str,
    sha256_hash: str,
) -> FileVersion | None:
    result = await session.execute(
        select(FileVersion)
        .join(
            ProjectFile,
            and_(
                ProjectFile.id == FileVersion.file_id,
                ProjectFile.current_version == FileVersion.version_number,
            ),
        )
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.deleted_at.is_(None),
            ProjectFile.original_filename == original_filename,
            ProjectFile.sha256_hash == sha256_hash,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


def _copy_zip_member_to_spooled_file(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> tuple[BinaryIO, int, str]:
    sha256 = hashlib.sha256()
    size_bytes = 0
    spooled = SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES, mode="w+b")

    with archive.open(info) as member:
        while chunk := member.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > MAX_UPLOAD_BYTES:
                spooled.close()
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"{info.filename} është më i madh se kufiri prej 50 MB.",
                )
            sha256.update(chunk)
            spooled.write(chunk)

    if size_bytes == 0:
        spooled.close()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{info.filename} është bosh.",
        )

    spooled.seek(0)
    return spooled, size_bytes, sha256.hexdigest()


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


async def get_file_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> FileVersion:
    await get_project_file(session, project_id=project_id, file_id=file_id, user_id=user_id)
    result = await session.execute(
        select(FileVersion).where(
            FileVersion.id == version_id,
            FileVersion.file_id == file_id,
        )
    )
    file_version = result.scalar_one_or_none()
    if file_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versioni i dokumentit nuk u gjet.",
        )
    return file_version


async def reprocess_file_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> FileVersion:
    file_version = await get_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=user_id,
    )
    file_version.parse_status = "pending"
    await session.commit()
    await session.refresh(file_version)
    parse_file_version.send(str(file_version.id))
    return file_version


async def list_document_chunks_for_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[DocumentChunk]:
    await get_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=user_id,
    )
    result = await session.execute(
        select(DocumentChunk)
        .where(DocumentChunk.file_version_id == version_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(result.scalars())


async def get_parsed_document_for_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ParsedDocument:
    await get_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=user_id,
    )
    result = await session.execute(
        select(ParsedDocument).where(ParsedDocument.file_version_id == version_id)
    )
    parsed_document = result.scalar_one_or_none()
    if parsed_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dokumenti nuk është përpunuar ende.",
        )
    return parsed_document


async def classify_parsed_document_for_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ParsedDocument:
    file_version = await get_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=version_id,
        user_id=user_id,
    )
    parsed_document = await _get_parsed_document_by_version_id(
        session,
        version_id=version_id,
    )
    _apply_document_classification(parsed_document, file_version)
    await session.commit()
    await session.refresh(parsed_document)
    return parsed_document


async def get_parsed_document_for_current_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ParsedDocument:
    file_version = await _get_current_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=user_id,
    )
    return await get_parsed_document_for_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=file_version.id,
        user_id=user_id,
    )


async def classify_parsed_document_for_current_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ParsedDocument:
    file_version = await _get_current_file_version(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=user_id,
    )
    return await classify_parsed_document_for_version(
        session,
        project_id=project_id,
        file_id=file_id,
        version_id=file_version.id,
        user_id=user_id,
    )


async def classify_project_current_documents(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    await get_project(session, project_id=project_id, user_id=user_id)
    rows = await _get_current_project_file_rows(session, project_id=project_id)
    reclassified_files = 0

    for _, file_version, parsed_document in rows:
        if parsed_document is None:
            continue
        before_type = parsed_document.document_type
        before_metadata = dict(parsed_document.document_metadata or {})
        _apply_document_classification(parsed_document, file_version)
        if (
            parsed_document.document_type != before_type
            or parsed_document.document_metadata != before_metadata
        ):
            reclassified_files += 1

    await session.commit()
    return _build_classification_summary(rows, reclassified_files=reclassified_files)


async def get_project_classification_summary(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict:
    await get_project(session, project_id=project_id, user_id=user_id)
    rows = await _get_current_project_file_rows(session, project_id=project_id)
    return _build_classification_summary(rows, reclassified_files=0)


async def _get_current_project_file_rows(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
) -> list[tuple[ProjectFile, FileVersion, ParsedDocument | None]]:
    result = await session.execute(
        select(ProjectFile, FileVersion, ParsedDocument)
        .join(
            FileVersion,
            FileVersion.file_id == ProjectFile.id,
        )
        .outerjoin(ParsedDocument, ParsedDocument.file_version_id == FileVersion.id)
        .where(
            ProjectFile.project_id == project_id,
            ProjectFile.deleted_at.is_(None),
            FileVersion.version_number == ProjectFile.current_version,
        )
        .order_by(ProjectFile.created_at.desc())
    )
    return list(result)


def _build_classification_summary(
    rows: list[tuple[ProjectFile, FileVersion, ParsedDocument | None]],
    *,
    reclassified_files: int,
) -> dict:
    status_counts = Counter(file_version.parse_status for _, file_version, _ in rows)
    document_type_counts: Counter[str] = Counter()
    files: list[dict] = []

    for project_file, file_version, parsed_document in rows:
        document_type = parsed_document.document_type if parsed_document else None
        confidence = _classification_confidence(parsed_document)
        if is_parsed_status(file_version.parse_status) and document_type:
            document_type_counts[document_type] += 1

        files.append(
            {
                "file_id": project_file.id,
                "version_id": file_version.id,
                "filename": file_version.original_filename,
                "parse_status": file_version.parse_status,
                "document_type": document_type,
                "classification_confidence": confidence,
            }
        )

    unknown_files = document_type_counts.get(UNKNOWN_DOCUMENT_TYPE, 0)
    classified_files = sum(document_type_counts.values()) - unknown_files
    return {
        "total_files": len(rows),
        "parsed_files": sum(status_counts.get(value, 0) for value in PARSED_STATUSES),
        "pending_files": status_counts.get("pending", 0),
        "processing_files": status_counts.get("processing", 0),
        "unsupported_files": status_counts.get("unsupported", 0),
        "failed_files": status_counts.get("failed", 0),
        "unknown_files": unknown_files,
        "classified_files": classified_files,
        "reclassified_files": reclassified_files,
        "document_type_counts": dict(sorted(document_type_counts.items())),
        "files": files,
    }


async def _get_current_file_version(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    user_id: uuid.UUID,
) -> FileVersion:
    project_file = await get_project_file(
        session,
        project_id=project_id,
        file_id=file_id,
        user_id=user_id,
    )
    result = await session.execute(
        select(FileVersion).where(
            FileVersion.file_id == file_id,
            FileVersion.version_number == project_file.current_version,
        )
    )
    file_version = result.scalar_one_or_none()
    if file_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Versioni aktual i dokumentit nuk u gjet.",
        )
    return file_version


async def _get_parsed_document_by_version_id(
    session: AsyncSession,
    *,
    version_id: uuid.UUID,
) -> ParsedDocument:
    result = await session.execute(
        select(ParsedDocument).where(ParsedDocument.file_version_id == version_id)
    )
    parsed_document = result.scalar_one_or_none()
    if parsed_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dokumenti nuk është përpunuar ende.",
        )
    return parsed_document


def _apply_document_classification(
    parsed_document: ParsedDocument,
    file_version: FileVersion,
) -> None:
    classification = classify_document(
        file_version.original_filename,
        parsed_document.text_content,
    )
    metadata = dict(parsed_document.document_metadata or {})
    metadata["classification"] = classification.as_metadata()

    parsed_document.document_type = classification.document_type
    parsed_document.document_metadata = metadata


def _classification_confidence(parsed_document: ParsedDocument | None) -> float | None:
    if parsed_document is None:
        return None
    metadata = parsed_document.document_metadata or {}
    classification = metadata.get("classification") or {}
    confidence = classification.get("confidence")
    if confidence is None:
        return None
    try:
        return float(confidence)
    except (TypeError, ValueError):
        return None
