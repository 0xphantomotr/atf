import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.core.config import settings
from app.files import service as files_service
from app.files.models import FileVersion, ProjectFile
from app.google_drive.links import GoogleDriveLinkError, extract_google_drive_folder_id
from app.google_drive.metadata import (
    FOLDER_MIME_TYPE,
    GOOGLE_EXPORTS,
    DriveFileRef,
    drive_ref_changed,
    get_folder,
    list_folder_tree,
    next_output_filename,
    preflight_counts,
    supported_ref,
)
from app.google_drive.models import GoogleDriveFileSync, GoogleDriveProjectBinding
from app.google_drive.oauth import get_google_drive_connection, google_credentials
from app.projects.service import get_project
from app.prompting.attachments import PersistedPromptUpload
from app.reviews import service as reviews_service

class GoogleDriveError(RuntimeError):
    pass


class _LimitedBuffer(BytesIO):
    def __init__(self, *, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, value: bytes) -> int:
        if self.tell() + len(value) > self.limit:
            raise GoogleDriveError("Një dokument në Google Drive tejkalon kufirin 50 MB.")
        return super().write(value)


@dataclass(frozen=True)
class DriveFolderImportResult:
    project_id: UUID
    folder_id: str
    folder_name: str
    version_ids: tuple[UUID, ...]
    uploaded_count: int
    reused_count: int
    changed_count: int
    deleted_count: int
    skipped: tuple[dict[str, str], ...]

    def as_step_data(self) -> dict:
        return {
            "project_id": str(self.project_id),
            "drive_folder_id": self.folder_id,
            "drive_folder_name": self.folder_name,
            "file_version_ids": [str(value) for value in self.version_ids],
            "uploaded_count": self.uploaded_count,
            "reused_count": self.reused_count,
            "changed_count": self.changed_count,
            "deleted_count": self.deleted_count,
            "skipped_count": len(self.skipped),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class DriveUploadResult:
    file_id: str
    filename: str
    web_view_link: str
    reused: bool
    output_folder_id: str
    output_folder_name: str


@dataclass(frozen=True)
class DriveFolderPreflightResult:
    project_id: UUID
    folder_id: str
    folder_name: str
    readable: bool
    writable: bool
    supported_count: int
    skipped_count: int
    new_count: int
    changed_count: int
    unchanged_count: int
    deleted_count: int
    skipped: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class DriveBindingStatus:
    project_id: UUID
    project_name: str
    folder_id: str
    folder_name: str
    folder_url: str
    sync_status: str
    last_sync_completed_at: datetime | None
    last_sync_summary: dict


async def import_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    folder_url: str,
) -> DriveFolderImportResult:
    await bind_google_drive_folder(
        session,
        project_id=project_id,
        user_id=user_id,
        folder_url=folder_url,
    )
    return await sync_google_drive_folder(
        session,
        project_id=project_id,
        user_id=user_id,
    )


async def bind_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    folder_url: str,
) -> DriveBindingStatus:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    drive = await _authorized_drive(session, user_id=user_id)
    try:
        folder_id = extract_google_drive_folder_id(folder_url)
    except GoogleDriveLinkError as exc:
        raise GoogleDriveError(str(exc)) from exc
    try:
        folder = await asyncio.to_thread(_get_folder, drive, folder_id)
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "Google Drive nuk mund ta lexonte folderin. Kontrolloni lidhjen dhe lejet."
        ) from exc

    conflict = await session.execute(
        select(GoogleDriveProjectBinding).where(
            GoogleDriveProjectBinding.user_id == user_id,
            GoogleDriveProjectBinding.folder_id == folder_id,
            GoogleDriveProjectBinding.project_id != project.id,
        )
    )
    if conflict.scalar_one_or_none() is not None:
        raise GoogleDriveError(
            "Ky folder Google Drive është lidhur tashmë me një projekt tjetër."
        )

    binding = await _project_binding(session, project_id=project.id, user_id=user_id)
    replaced_folder_id: str | None = None
    if binding is None:
        binding = GoogleDriveProjectBinding(
            project_id=project.id,
            user_id=user_id,
            folder_id=folder_id,
            folder_name=str(folder.get("name") or "Dosja Teknike"),
            folder_url=folder_url,
            sync_status="never_synced",
            last_sync_summary={},
        )
        session.add(binding)
    elif binding.folder_id != folder_id:
        replaced_folder_id = binding.folder_id
        await _retire_binding_files(session, binding=binding)
        await session.execute(
            delete(GoogleDriveFileSync).where(
                GoogleDriveFileSync.binding_id == binding.id
            )
        )
        binding.folder_id = folder_id
        binding.folder_name = str(folder.get("name") or "Dosja Teknike")
        binding.folder_url = folder_url
        binding.output_folder_id = None
        binding.sync_status = "never_synced"
        binding.last_sync_started_at = None
        binding.last_sync_completed_at = None
        binding.last_sync_summary = {}
    else:
        binding.folder_name = str(folder.get("name") or binding.folder_name)
        binding.folder_url = folder_url

    await session.flush()
    await write_audit_log(
        session,
        action="google_drive.folder.bound",
        entity_type="project",
        entity_id=project.id,
        actor_user_id=user_id,
        project_id=project.id,
        details={
            "folder_id": folder_id,
            "folder_name": binding.folder_name,
            "replaced_folder_id": replaced_folder_id,
        },
    )
    await session.commit()
    return _binding_status(project.name, binding)


async def unbind_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> bool:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    binding = await _project_binding(session, project_id=project.id, user_id=user_id)
    if binding is None:
        return False
    await session.execute(
        delete(GoogleDriveFileSync).where(GoogleDriveFileSync.binding_id == binding.id)
    )
    await session.delete(binding)
    await write_audit_log(
        session,
        action="google_drive.folder.unbound",
        entity_type="project",
        entity_id=project.id,
        actor_user_id=user_id,
        project_id=project.id,
        details={"folder_id": binding.folder_id, "folder_name": binding.folder_name},
    )
    await session.commit()
    return True


async def google_drive_binding_status(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> DriveBindingStatus | None:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    binding = await _project_binding(session, project_id=project.id, user_id=user_id)
    return _binding_status(project.name, binding) if binding is not None else None


async def preflight_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    folder_url: str | None = None,
) -> DriveFolderPreflightResult:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    binding = await _project_binding(session, project_id=project.id, user_id=user_id)
    folder_id = _optional_folder_id(folder_url)
    if folder_id is None:
        if binding is None:
            raise GoogleDriveError(
                "Projekti nuk ka folder Google Drive të lidhur. Përdorni /google_folder."
            )
        folder_id = binding.folder_id
    drive = await _authorized_drive(session, user_id=user_id)
    try:
        folder, refs = await asyncio.to_thread(_list_folder_tree, drive, folder_id)
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "Google Drive nuk mund ta lexonte folderin. Kontrolloni lidhjen dhe lejet."
        ) from exc

    manifest = await _manifest_by_drive_id(
        session,
        binding_id=binding.id if binding is not None and binding.folder_id == folder_id else None,
    )
    counts, skipped = _preflight_counts(refs, manifest)
    active_ids = {
        ref.file_id for ref in refs if _supported_ref(ref)[2] is None
    }
    deleted_count = sum(
        1
        for drive_id, row in manifest.items()
        if row.status == "active" and drive_id not in active_ids
    )
    capabilities = folder.get("capabilities") or {}
    return DriveFolderPreflightResult(
        project_id=project.id,
        folder_id=folder_id,
        folder_name=str(folder.get("name") or "Dosja Teknike"),
        readable=True,
        writable=capabilities.get("canAddChildren") is True,
        supported_count=counts["new"] + counts["changed"] + counts["unchanged"],
        skipped_count=len(skipped),
        new_count=counts["new"],
        changed_count=counts["changed"],
        unchanged_count=counts["unchanged"],
        deleted_count=deleted_count,
        skipped=tuple(skipped),
    )


async def sync_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> DriveFolderImportResult:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    binding = await _project_binding(session, project_id=project.id, user_id=user_id)
    if binding is None:
        raise GoogleDriveError(
            "Projekti nuk ka folder Google Drive të lidhur. Përdorni /google_folder."
        )
    drive = await _authorized_drive(session, user_id=user_id)
    started_at = datetime.now(timezone.utc)
    binding.sync_status = "syncing"
    binding.last_sync_started_at = started_at
    await session.commit()
    try:
        folder, refs = await asyncio.to_thread(_list_folder_tree, drive, binding.folder_id)
    except Exception as exc:
        binding.sync_status = "failed"
        binding.last_sync_summary = {"error": str(exc)[:500]}
        await session.commit()
        if isinstance(exc, GoogleDriveError):
            raise
        raise GoogleDriveError(
            "Google Drive nuk mund ta lexonte folderin. Kontrolloni lidhjen dhe lejet."
        ) from exc

    manifest = await _manifest_by_drive_id(session, binding_id=binding.id)
    seen_ids: set[str] = set()
    version_ids: list[UUID] = []
    new_count = 0
    changed_count = 0
    unchanged_count = 0
    deleted_count = 0
    skipped: list[dict[str, str]] = []
    total_bytes = 0
    scan_complete = True
    now = datetime.now(timezone.utc)

    for ref in refs:
        seen_ids.add(ref.file_id)
        output_name, output_mime, reason = _supported_ref(ref)
        row = manifest.get(ref.file_id)
        if reason is not None:
            skipped.append({"filename": ref.relative_path, "reason": reason})
            row = _update_manifest_row(
                session,
                row=row,
                binding=binding,
                ref=ref,
                status="skipped",
                skip_reason=reason,
                now=now,
            )
            manifest[ref.file_id] = row
            continue

        if row is not None and not _drive_ref_changed(row, ref):
            unchanged_count += 1
            row.relative_path = output_name
            row.mime_type = output_mime
            row.status = "active"
            row.skip_reason = None
            row.last_seen_at = now
            await _rename_project_file_if_needed(
                session,
                row=row,
                output_name=output_name,
                output_mime=output_mime,
            )
            if row.file_version_id is not None:
                version_ids.append(row.file_version_id)
            continue

        exported = GOOGLE_EXPORTS.get(ref.mime_type)
        try:
            content = await asyncio.to_thread(
                _download_drive_file,
                drive,
                ref,
                export_mime=exported[0] if exported else None,
            )
        except GoogleDriveError as exc:
            reason = str(exc)
            skipped.append({"filename": output_name, "reason": reason})
            row = _update_manifest_row(
                session,
                row=row,
                binding=binding,
                ref=ref,
                status="failed",
                skip_reason=reason,
                now=now,
            )
            manifest[ref.file_id] = row
            continue
        total_bytes += len(content)
        if total_bytes > settings.google_drive_max_total_bytes:
            skipped.append(
                {
                    "filename": output_name,
                    "reason": (
                        "kufiri total i importimit u arrit: "
                        f"{settings.google_drive_max_total_bytes // (1024 * 1024)} MB"
                    ),
                }
            )
            scan_complete = False
            break

        sha256_hash = hashlib.sha256(content).hexdigest()
        upload = PersistedPromptUpload(
            filename=output_name,
            content_type=output_mime,
            content=content,
        )
        project_file = await _active_manifest_project_file(session, row=row)
        if project_file is not None:
            if project_file.sha256_hash != sha256_hash:
                version = await files_service.create_file_version(
                    session,
                    project_id=project.id,
                    file_id=project_file.id,
                    upload=upload,
                    user_id=user_id,
                )
                changed_count += 1
            else:
                version = (
                    await session.get(FileVersion, row.file_version_id)
                    if row is not None and row.file_version_id is not None
                    else None
                )
                if version is None:
                    raise GoogleDriveError(
                        "Versioni lokal i dokumentit të pandryshuar nuk u gjet."
                    )
                project_file.original_filename = output_name
                project_file.normalized_filename = files_service.normalize_filename(
                    output_name
                )
                project_file.mime_type = output_mime
                unchanged_count += 1
        else:
            existing = await files_service.find_current_file_version_by_identity(
                session,
                project_id=project.id,
                original_filename=output_name,
                sha256_hash=sha256_hash,
            )
            if existing is not None:
                version = existing
                project_file = await session.get(ProjectFile, existing.file_id)
                unchanged_count += 1
            else:
                project_file, version = await files_service.create_project_file(
                    session,
                    project_id=project.id,
                    upload=upload,
                    user_id=user_id,
                )
                new_count += 1
        row = _update_manifest_row(
            session,
            row=row,
            binding=binding,
            ref=ref,
            status="active",
            skip_reason=None,
            now=now,
        )
        row.relative_path = output_name
        row.mime_type = output_mime
        row.project_file_id = project_file.id if project_file is not None else None
        row.file_version_id = version.id
        row.last_synced_at = now
        manifest[ref.file_id] = row
        version_ids.append(version.id)

    for drive_id, row in manifest.items():
        if not scan_complete:
            break
        if drive_id in seen_ids or row.status == "source_deleted":
            continue
        if row.status == "active":
            deleted_count += 1
            project_file = await _active_manifest_project_file(session, row=row)
            if project_file is not None:
                project_file.deleted_at = now
        row.status = "source_deleted"
        row.last_seen_at = now

    if not version_ids:
        binding.sync_status = "failed"
        binding.last_sync_summary = {"error": "no_supported_documents"}
        await session.commit()
        raise GoogleDriveError(
            "Folderi nuk përmban dokumente të mbështetura ose dokumentet nuk mund të lexohen."
        )

    completed_at = datetime.now(timezone.utc)
    summary = {
        "new_count": new_count,
        "changed_count": changed_count,
        "unchanged_count": unchanged_count,
        "deleted_count": deleted_count,
        "skipped_count": len(skipped),
        "completed_at": completed_at.isoformat(),
    }
    binding.folder_name = str(folder.get("name") or binding.folder_name)
    binding.sync_status = "completed"
    binding.last_sync_completed_at = completed_at
    binding.last_sync_summary = summary
    await write_audit_log(
        session,
        action="google_drive.folder.synced",
        entity_type="project",
        entity_id=project.id,
        actor_user_id=user_id,
        project_id=project.id,
        details={"folder_id": binding.folder_id, **summary},
    )
    await session.commit()
    return DriveFolderImportResult(
        project_id=project.id,
        folder_id=binding.folder_id,
        folder_name=binding.folder_name,
        version_ids=tuple(dict.fromkeys(version_ids)),
        uploaded_count=new_count,
        reused_count=unchanged_count,
        changed_count=changed_count,
        deleted_count=deleted_count,
        skipped=tuple(skipped),
    )


async def _project_binding(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
) -> GoogleDriveProjectBinding | None:
    result = await session.execute(
        select(GoogleDriveProjectBinding).where(
            GoogleDriveProjectBinding.project_id == project_id,
            GoogleDriveProjectBinding.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _manifest_by_drive_id(
    session: AsyncSession,
    *,
    binding_id: UUID | None,
) -> dict[str, GoogleDriveFileSync]:
    if binding_id is None:
        return {}
    result = await session.execute(
        select(GoogleDriveFileSync).where(GoogleDriveFileSync.binding_id == binding_id)
    )
    return {row.drive_file_id: row for row in result.scalars()}


def _binding_status(
    project_name: str,
    binding: GoogleDriveProjectBinding,
) -> DriveBindingStatus:
    return DriveBindingStatus(
        project_id=binding.project_id,
        project_name=project_name,
        folder_id=binding.folder_id,
        folder_name=binding.folder_name,
        folder_url=binding.folder_url,
        sync_status=binding.sync_status,
        last_sync_completed_at=binding.last_sync_completed_at,
        last_sync_summary=dict(binding.last_sync_summary or {}),
    )


def _optional_folder_id(folder_url: str | None) -> str | None:
    if folder_url is None or not folder_url.strip():
        return None
    try:
        return extract_google_drive_folder_id(folder_url)
    except GoogleDriveLinkError as exc:
        raise GoogleDriveError(str(exc)) from exc


def _supported_ref(ref: DriveFileRef) -> tuple[str, str, str | None]:
    return supported_ref(ref, max_upload_bytes=files_service.MAX_UPLOAD_BYTES)


def _preflight_counts(
    refs: list[DriveFileRef],
    manifest: dict[str, GoogleDriveFileSync],
) -> tuple[dict[str, int], list[dict[str, str]]]:
    return preflight_counts(
        refs,
        manifest,
        max_upload_bytes=files_service.MAX_UPLOAD_BYTES,
    )


def _drive_ref_changed(row: GoogleDriveFileSync, ref: DriveFileRef) -> bool:
    return drive_ref_changed(row, ref)


def _update_manifest_row(
    session: AsyncSession,
    *,
    row: GoogleDriveFileSync | None,
    binding: GoogleDriveProjectBinding,
    ref: DriveFileRef,
    status: str,
    skip_reason: str | None,
    now: datetime,
) -> GoogleDriveFileSync:
    if row is None:
        row = GoogleDriveFileSync(
            binding_id=binding.id,
            drive_file_id=ref.file_id,
            relative_path=ref.relative_path,
            mime_type=ref.mime_type,
            status=status,
            last_seen_at=now,
        )
        session.add(row)
    row.relative_path = ref.relative_path
    row.mime_type = ref.mime_type
    row.size_bytes = ref.size_bytes
    row.modified_time = ref.modified_time
    row.md5_checksum = ref.md5_checksum
    row.drive_version = ref.drive_version
    row.status = status
    row.skip_reason = skip_reason
    row.last_seen_at = now
    return row


async def _active_manifest_project_file(
    session: AsyncSession,
    *,
    row: GoogleDriveFileSync | None,
) -> ProjectFile | None:
    if row is None or row.project_file_id is None:
        return None
    project_file = await session.get(ProjectFile, row.project_file_id)
    if project_file is None or project_file.deleted_at is not None:
        return None
    return project_file


async def _rename_project_file_if_needed(
    session: AsyncSession,
    *,
    row: GoogleDriveFileSync,
    output_name: str,
    output_mime: str,
) -> None:
    project_file = await _active_manifest_project_file(session, row=row)
    if project_file is None or (
        project_file.original_filename == output_name and project_file.mime_type == output_mime
    ):
        return
    project_file.original_filename = output_name
    project_file.normalized_filename = files_service.normalize_filename(output_name)
    project_file.mime_type = output_mime


async def _retire_binding_files(
    session: AsyncSession,
    *,
    binding: GoogleDriveProjectBinding,
) -> None:
    rows = await _manifest_by_drive_id(session, binding_id=binding.id)
    now = datetime.now(timezone.utc)
    for row in rows.values():
        project_file = await _active_manifest_project_file(session, row=row)
        if project_file is not None:
            project_file.deleted_at = now



async def upload_generated_pdf_to_drive(
    session: AsyncSession,
    *,
    output_id: UUID,
    expected_review_job_id: UUID,
    prompt_run_id: UUID,
    user_id: UUID,
    folder_url: str | None = None,
) -> DriveUploadResult:
    output = await reviews_service.get_generated_output(
        session,
        output_id=output_id,
        user_id=user_id,
    )
    if output.review_job_id != expected_review_job_id or output.output_type != "pdf":
        raise GoogleDriveError("PDF-ja nuk i përket gjenerimit të kësaj kërkese.")
    project = await get_project(
        session,
        project_id=output.project_id,
        user_id=user_id,
    )
    binding = await _project_binding(
        session,
        project_id=project.id,
        user_id=user_id,
    )
    explicit_folder_id = _optional_folder_id(folder_url)
    if binding is None and folder_url is not None:
        await bind_google_drive_folder(
            session,
            project_id=project.id,
            user_id=user_id,
            folder_url=folder_url,
        )
        binding = await _project_binding(
            session,
            project_id=project.id,
            user_id=user_id,
        )
    if binding is None:
        raise GoogleDriveError(
            "Projekti nuk ka folder Google Drive të lidhur. Përdorni /google_folder."
        )
    target_root_folder_id = explicit_folder_id or binding.folder_id
    target_is_bound_folder = target_root_folder_id == binding.folder_id
    drive = await _authorized_drive(session, user_id=user_id)
    try:
        await asyncio.to_thread(_require_writable_folder, drive, target_root_folder_id)
        output_folder = await asyncio.to_thread(
            _ensure_output_folder,
            drive,
            root_folder_id=target_root_folder_id,
            known_output_folder_id=(
                binding.output_folder_id if target_is_bound_folder else None
            ),
        )
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "Google Drive nuk mund të verifikonte lejet e folderit."
        ) from exc
    output_folder_id = str(output_folder["id"])
    if target_is_bound_folder:
        binding.output_folder_id = output_folder_id
    download = await reviews_service.download_generated_output(
        session,
        output_id=output.id,
        user_id=user_id,
    )
    try:
        versioned_filename = await asyncio.to_thread(
            _next_output_filename,
            drive,
            folder_id=output_folder_id,
            project_name=project.name,
        )
        result = await asyncio.to_thread(
            _upload_pdf,
            drive,
            folder_id=output_folder_id,
            filename=versioned_filename,
            content=download.content,
            review_job_id=str(expected_review_job_id),
            prompt_run_id=str(prompt_run_id),
        )
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "PDF-ja nuk mund të ngarkohej në Google Drive. Kontrolloni lejet dhe provoni përsëri."
        ) from exc
    await write_audit_log(
        session,
        action="google_drive.report.uploaded",
        entity_type="generated_output",
        entity_id=output.id,
        actor_user_id=user_id,
        project_id=output.project_id,
        details={
            "folder_id": target_root_folder_id,
            "output_folder_id": output_folder_id,
            "drive_file_id": result.file_id,
            "review_job_id": str(expected_review_job_id),
            "reused": result.reused,
        },
    )
    await session.commit()
    return result


async def _authorized_drive(session: AsyncSession, *, user_id: UUID):
    connection = await get_google_drive_connection(session, user_id=user_id)
    if connection is None:
        raise GoogleDriveError(
            "Google Drive nuk është lidhur. Përdorni /google_connect dhe provoni përsëri."
        )
    credentials = google_credentials(connection)
    return await asyncio.to_thread(_build_drive, credentials)


def _build_drive(credentials):
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _list_folder_tree(drive, folder_id: str) -> tuple[dict, list[DriveFileRef]]:
    try:
        return list_folder_tree(
            drive,
            folder_id,
            max_files=settings.google_drive_max_files,
        )
    except ValueError as exc:
        raise GoogleDriveError(str(exc)) from exc


def _get_folder(drive, folder_id: str) -> dict:
    try:
        return get_folder(drive, folder_id)
    except Exception as exc:
        raise GoogleDriveError(
            "Folderi Google Drive nuk u gjet ose llogaria e lidhur nuk ka akses."
        ) from exc


def _require_writable_folder(drive, folder_id: str) -> dict:
    folder = _get_folder(drive, folder_id)
    capabilities = folder.get("capabilities") or {}
    if capabilities.get("canAddChildren") is not True:
        raise GoogleDriveError(
            "Llogaria e lidhur mund ta lexojë folderin, por nuk mund të ngarkojë skedarë aty."
        )
    return folder


def _download_drive_file(
    drive,
    ref: DriveFileRef,
    *,
    export_mime: str | None,
) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    request = (
        drive.files().export_media(fileId=ref.file_id, mimeType=export_mime)
        if export_mime
        else drive.files().get_media(fileId=ref.file_id, supportsAllDrives=True)
    )
    buffer = _LimitedBuffer(limit=files_service.MAX_UPLOAD_BYTES)
    try:
        downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError("dokumenti nuk mund të shkarkohej nga Google Drive") from exc
    return buffer.getvalue()


def _upload_pdf(
    drive,
    *,
    folder_id: str,
    filename: str,
    content: bytes,
    review_job_id: str,
    prompt_run_id: str,
) -> DriveUploadResult:
    from googleapiclient.http import MediaIoBaseUpload

    escaped_job_id = review_job_id.replace("'", "\\'")
    existing = (
        drive.files()
        .list(
            q=(
                f"'{folder_id}' in parents and trashed = false and "
                f"appProperties has {{ key='atf_review_job_id' and value='{escaped_job_id}' }}"
            ),
            fields="files(id,name,webViewLink)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if existing:
        item = existing[0]
        return DriveUploadResult(
            file_id=str(item["id"]),
            filename=str(item.get("name") or filename),
            web_view_link=str(item.get("webViewLink") or ""),
            reused=True,
            output_folder_id=folder_id,
            output_folder_name="Kolaudimi",
        )

    media = MediaIoBaseUpload(BytesIO(content), mimetype="application/pdf", resumable=True)
    item = (
        drive.files()
        .create(
            body={
                "name": filename,
                "parents": [folder_id],
                "appProperties": {
                    "atf_review_job_id": review_job_id,
                    "atf_prompt_run_id": prompt_run_id,
                },
            },
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    return DriveUploadResult(
        file_id=str(item["id"]),
        filename=str(item.get("name") or filename),
        web_view_link=str(item.get("webViewLink") or ""),
        reused=False,
        output_folder_id=folder_id,
        output_folder_name="Kolaudimi",
    )


def _ensure_output_folder(
    drive,
    *,
    root_folder_id: str,
    known_output_folder_id: str | None,
) -> dict:
    if known_output_folder_id:
        try:
            folder = _require_writable_folder(drive, known_output_folder_id)
            if (
                folder.get("mimeType") == FOLDER_MIME_TYPE
                and root_folder_id in (folder.get("parents") or [])
            ):
                return folder
        except GoogleDriveError:
            pass
    escaped_root = root_folder_id.replace("'", "\\'")
    existing = (
        drive.files()
        .list(
            q=(
                f"'{escaped_root}' in parents and trashed = false and "
                f"mimeType = '{FOLDER_MIME_TYPE}' and "
                "appProperties has { key='atf_output_folder' and value='true' }"
            ),
            fields="files(id,name,mimeType,capabilities(canAddChildren))",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    if existing:
        return _require_writable_folder(drive, str(existing[0]["id"]))
    return (
        drive.files()
        .create(
            body={
                "name": "Kolaudimi",
                "mimeType": FOLDER_MIME_TYPE,
                "parents": [root_folder_id],
                "appProperties": {"atf_output_folder": "true"},
            },
            fields="id,name,mimeType,capabilities(canAddChildren)",
            supportsAllDrives=True,
        )
        .execute()
    )


def _next_output_filename(
    drive,
    *,
    folder_id: str,
    project_name: str,
) -> str:
    return next_output_filename(
        drive,
        folder_id=folder_id,
        project_name=project_name,
    )
