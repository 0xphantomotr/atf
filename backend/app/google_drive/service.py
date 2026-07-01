import asyncio
import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_log.service import write_audit_log
from app.core.config import settings
from app.files import service as files_service
from app.files.parser import is_supported_filename
from app.google_drive.links import GoogleDriveLinkError, extract_google_drive_folder_id
from app.google_drive.oauth import get_google_drive_connection, google_credentials
from app.projects.service import get_project
from app.prompting.attachments import PersistedPromptUpload
from app.reviews import service as reviews_service

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
GOOGLE_MIME_PREFIX = "application/vnd.google-apps."

GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}


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
class DriveFileRef:
    file_id: str
    relative_path: str
    mime_type: str
    size_bytes: int | None


@dataclass(frozen=True)
class DriveFolderImportResult:
    project_id: UUID
    folder_id: str
    folder_name: str
    version_ids: tuple[UUID, ...]
    uploaded_count: int
    reused_count: int
    skipped: tuple[dict[str, str], ...]

    def as_step_data(self) -> dict:
        return {
            "project_id": str(self.project_id),
            "drive_folder_id": self.folder_id,
            "drive_folder_name": self.folder_name,
            "file_version_ids": [str(value) for value in self.version_ids],
            "uploaded_count": self.uploaded_count,
            "reused_count": self.reused_count,
            "skipped_count": len(self.skipped),
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class DriveUploadResult:
    file_id: str
    filename: str
    web_view_link: str
    reused: bool


async def import_google_drive_folder(
    session: AsyncSession,
    *,
    project_id: UUID,
    user_id: UUID,
    folder_url: str,
) -> DriveFolderImportResult:
    project = await get_project(session, project_id=project_id, user_id=user_id)
    drive = await _authorized_drive(session, user_id=user_id)
    try:
        folder_id = extract_google_drive_folder_id(folder_url)
    except GoogleDriveLinkError as exc:
        raise GoogleDriveError(str(exc)) from exc
    try:
        folder, refs = await asyncio.to_thread(_list_folder_tree, drive, folder_id)
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "Google Drive nuk mund ta lexonte folderin. Kontrolloni lidhjen dhe lejet."
        ) from exc

    uploaded_ids: list[UUID] = []
    reused_ids: list[UUID] = []
    skipped: list[dict[str, str]] = []
    total_bytes = 0
    for ref in refs:
        exported = GOOGLE_EXPORTS.get(ref.mime_type)
        output_name = ref.relative_path
        output_mime = ref.mime_type
        if exported is not None:
            output_mime, suffix = exported
            output_name = _replace_suffix(output_name, suffix)
        elif ref.mime_type.startswith(GOOGLE_MIME_PREFIX):
            skipped.append({"filename": output_name, "reason": "format Google i pambështetur"})
            continue
        if not is_supported_filename(output_name):
            skipped.append({"filename": output_name, "reason": "format i pambështetur"})
            continue
        if ref.size_bytes is not None and ref.size_bytes > files_service.MAX_UPLOAD_BYTES:
            skipped.append({"filename": output_name, "reason": "tejkalon kufirin 50 MB"})
            continue

        try:
            content = await asyncio.to_thread(
                _download_drive_file,
                drive,
                ref,
                export_mime=exported[0] if exported else None,
            )
        except GoogleDriveError as exc:
            skipped.append({"filename": output_name, "reason": str(exc)})
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
            break
        sha256_hash = hashlib.sha256(content).hexdigest()
        existing = await files_service.find_current_file_version_by_identity(
            session,
            project_id=project.id,
            original_filename=output_name,
            sha256_hash=sha256_hash,
        )
        if existing is not None:
            reused_ids.append(existing.id)
            continue

        upload = PersistedPromptUpload(
            filename=output_name,
            content_type=output_mime or "application/octet-stream",
            content=content,
        )
        _, version = await files_service.create_project_file(
            session,
            project_id=project.id,
            upload=upload,
            user_id=user_id,
        )
        uploaded_ids.append(version.id)

    version_ids = tuple(uploaded_ids + reused_ids)
    if not version_ids:
        raise GoogleDriveError(
            "Folderi nuk përmban dokumente të mbështetura ose dokumentet nuk mund të lexohen."
        )
    await write_audit_log(
        session,
        action="google_drive.folder.imported",
        entity_type="project",
        entity_id=project.id,
        actor_user_id=user_id,
        project_id=project.id,
        details={
            "folder_id": folder_id,
            "folder_name": folder["name"],
            "uploaded_count": len(uploaded_ids),
            "reused_count": len(reused_ids),
            "skipped_count": len(skipped),
        },
    )
    await session.commit()
    return DriveFolderImportResult(
        project_id=project.id,
        folder_id=folder_id,
        folder_name=str(folder["name"]),
        version_ids=version_ids,
        uploaded_count=len(uploaded_ids),
        reused_count=len(reused_ids),
        skipped=tuple(skipped),
    )


async def upload_generated_pdf_to_drive(
    session: AsyncSession,
    *,
    output_id: UUID,
    expected_review_job_id: UUID,
    prompt_run_id: UUID,
    user_id: UUID,
    folder_url: str,
) -> DriveUploadResult:
    drive = await _authorized_drive(session, user_id=user_id)
    try:
        folder_id = extract_google_drive_folder_id(folder_url)
    except GoogleDriveLinkError as exc:
        raise GoogleDriveError(str(exc)) from exc
    try:
        await asyncio.to_thread(_require_writable_folder, drive, folder_id)
    except GoogleDriveError:
        raise
    except Exception as exc:
        raise GoogleDriveError(
            "Google Drive nuk mund të verifikonte lejet e folderit."
        ) from exc
    output = await reviews_service.get_generated_output(
        session,
        output_id=output_id,
        user_id=user_id,
    )
    if output.review_job_id != expected_review_job_id or output.output_type != "pdf":
        raise GoogleDriveError("PDF-ja nuk i përket gjenerimit të kësaj kërkese.")
    download = await reviews_service.download_generated_output(
        session,
        output_id=output.id,
        user_id=user_id,
    )
    try:
        result = await asyncio.to_thread(
            _upload_pdf,
            drive,
            folder_id=folder_id,
            filename=download.filename,
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
            "folder_id": folder_id,
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
    root = _get_folder(drive, folder_id)
    refs: list[DriveFileRef] = []
    root_path = PurePosixPath(_safe_component(str(root.get("name") or "Dosja Teknike")))
    queue: list[tuple[str, PurePosixPath, int]] = [(folder_id, root_path, 0)]
    while queue:
        parent_id, parent_path, depth = queue.pop(0)
        if depth > 20:
            raise GoogleDriveError("Folderi ka më shumë se 20 nivele nënfolderësh.")
        page_token = None
        items: list[dict] = []
        while True:
            response = (
                drive.files()
                .list(
                    q=f"'{parent_id}' in parents and trashed = false",
                    fields="nextPageToken,files(id,name,mimeType,size)",
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            items.extend(item for item in response.get("files", []) if isinstance(item, dict))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        for item in sorted(items, key=_drive_item_sort_key):
            name = _safe_component(str(item.get("name") or "dokument"))
            relative = parent_path / name
            if item.get("mimeType") == FOLDER_MIME_TYPE:
                queue.append((str(item["id"]), relative, depth + 1))
                continue
            refs.append(
                DriveFileRef(
                    file_id=str(item["id"]),
                    relative_path=str(relative),
                    mime_type=str(item.get("mimeType") or "application/octet-stream"),
                    size_bytes=(
                        int(item["size"])
                        if str(item.get("size") or "").isdigit()
                        else None
                    ),
                )
            )
            if len(refs) > settings.google_drive_max_files:
                raise GoogleDriveError(
                    f"Folderi ka më shumë se {settings.google_drive_max_files} dokumente."
                )
    return root, refs


def _get_folder(drive, folder_id: str) -> dict:
    try:
        item = (
            drive.files()
            .get(
                fileId=folder_id,
                fields="id,name,mimeType,capabilities(canAddChildren)",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception as exc:
        raise GoogleDriveError(
            "Folderi Google Drive nuk u gjet ose llogaria e lidhur nuk ka akses."
        ) from exc
    if item.get("mimeType") != FOLDER_MIME_TYPE:
        raise GoogleDriveError("Linku duhet t'i përkasë një folderi Google Drive.")
    return item


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
    )


def _safe_component(value: str) -> str:
    cleaned = value.replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    return cleaned if cleaned not in {"", ".", ".."} else "dokument"


def _drive_item_sort_key(item: dict) -> tuple[int, tuple[object, ...]]:
    folder_rank = 0 if item.get("mimeType") == FOLDER_MIME_TYPE else 1
    return folder_rank, _natural_sort_key(str(item.get("name") or ""))


def _natural_sort_key(value: str) -> tuple[object, ...]:
    parts = re.split(r"(\d+)", value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _replace_suffix(filename: str, suffix: str) -> str:
    path = PurePosixPath(filename)
    if path.suffix:
        return str(path.with_suffix(suffix))
    return f"{filename}{suffix}"
