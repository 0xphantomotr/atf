import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol

from app.files.parser import is_supported_filename

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
GOOGLE_MIME_PREFIX = "application/vnd.google-apps."
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

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


class ManifestRow(Protocol):
    status: str
    file_version_id: object | None
    drive_version: str | None
    md5_checksum: str | None
    modified_time: datetime | None
    size_bytes: int | None


@dataclass(frozen=True)
class DriveFileRef:
    file_id: str
    relative_path: str
    mime_type: str
    size_bytes: int | None
    modified_time: datetime | None
    md5_checksum: str | None
    drive_version: str | None


def supported_ref(
    ref: DriveFileRef,
    *,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> tuple[str, str, str | None]:
    exported = GOOGLE_EXPORTS.get(ref.mime_type)
    output_name = ref.relative_path
    output_mime = ref.mime_type or "application/octet-stream"
    if exported is not None:
        output_mime, suffix = exported
        output_name = replace_suffix(output_name, suffix)
    elif ref.mime_type.startswith(GOOGLE_MIME_PREFIX):
        return output_name, output_mime, "format Google i pambështetur"
    if not is_supported_filename(output_name):
        return output_name, output_mime, "format i pambështetur"
    if ref.size_bytes is not None and ref.size_bytes > max_upload_bytes:
        return output_name, output_mime, "tejkalon kufirin 50 MB"
    return output_name, output_mime, None


def drive_ref_changed(row: ManifestRow, ref: DriveFileRef) -> bool:
    if row.status != "active" or row.file_version_id is None:
        return True
    if row.drive_version and ref.drive_version:
        return row.drive_version != ref.drive_version
    if row.md5_checksum and ref.md5_checksum:
        return row.md5_checksum != ref.md5_checksum
    if row.modified_time and ref.modified_time:
        return row.modified_time != ref.modified_time or row.size_bytes != ref.size_bytes
    return row.size_bytes != ref.size_bytes


def preflight_counts(
    refs: list[DriveFileRef],
    manifest: dict[str, ManifestRow],
    *,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> tuple[dict[str, int], list[dict[str, str]]]:
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    skipped: list[dict[str, str]] = []
    for ref in refs:
        _, _, reason = supported_ref(ref, max_upload_bytes=max_upload_bytes)
        if reason is not None:
            skipped.append({"filename": ref.relative_path, "reason": reason})
            continue
        row = manifest.get(ref.file_id)
        if row is None or row.status in {"source_deleted", "failed", "skipped"}:
            counts["new"] += 1
        elif drive_ref_changed(row, ref):
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    return counts, skipped


def list_folder_tree(
    drive,
    folder_id: str,
    *,
    max_files: int,
) -> tuple[dict, list[DriveFileRef]]:
    root = get_folder(drive, folder_id)
    refs: list[DriveFileRef] = []
    root_path = PurePosixPath(safe_component(str(root.get("name") or "Dosja Teknike")))
    queue: list[tuple[str, PurePosixPath, int]] = [(folder_id, root_path, 0)]
    while queue:
        parent_id, parent_path, depth = queue.pop(0)
        if depth > 20:
            raise ValueError("Folderi ka më shumë se 20 nivele nënfolderësh.")
        page_token = None
        items: list[dict] = []
        while True:
            response = (
                drive.files()
                .list(
                    q=f"'{parent_id}' in parents and trashed = false",
                    fields=(
                        "nextPageToken,files("
                        "id,name,mimeType,size,modifiedTime,md5Checksum,version,appProperties)"
                    ),
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
        for item in sorted(items, key=drive_item_sort_key):
            name = safe_component(str(item.get("name") or "dokument"))
            relative = parent_path / name
            properties = item.get("appProperties") or {}
            if item.get("mimeType") == FOLDER_MIME_TYPE:
                if properties.get("atf_output_folder") != "true":
                    queue.append((str(item["id"]), relative, depth + 1))
                continue
            if properties.get("atf_review_job_id"):
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
                    modified_time=parse_drive_datetime(item.get("modifiedTime")),
                    md5_checksum=str(item.get("md5Checksum") or "") or None,
                    drive_version=str(item.get("version") or "") or None,
                )
            )
            if len(refs) > max_files:
                raise ValueError(f"Folderi ka më shumë se {max_files} dokumente.")
    return root, refs


def get_folder(drive, folder_id: str) -> dict:
    item = (
        drive.files()
        .get(
            fileId=folder_id,
            fields=(
                "id,name,mimeType,webViewLink,"
                "parents,capabilities(canAddChildren,canDownload,canEdit)"
            ),
            supportsAllDrives=True,
        )
        .execute()
    )
    if item.get("mimeType") != FOLDER_MIME_TYPE:
        raise ValueError("Linku duhet t'i përkasë një folderi Google Drive.")
    return item


def safe_component(value: str) -> str:
    cleaned = value.replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    return cleaned if cleaned not in {"", ".", ".."} else "dokument"


def drive_item_sort_key(item: dict) -> tuple[int, tuple[object, ...]]:
    folder_rank = 0 if item.get("mimeType") == FOLDER_MIME_TYPE else 1
    return folder_rank, natural_sort_key(str(item.get("name") or ""))


def natural_sort_key(value: str) -> tuple[object, ...]:
    parts = re.split(r"(\d+)", value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def replace_suffix(filename: str, suffix: str) -> str:
    path = PurePosixPath(filename)
    if path.suffix:
        return str(path.with_suffix(suffix))
    return f"{filename}{suffix}"


def parse_drive_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_output_filename(
    drive,
    *,
    folder_id: str,
    project_name: str,
    generated_at: datetime | None = None,
) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9_-]+", "-", project_name).strip("-") or "projekti"
    date_part = (generated_at or datetime.now().astimezone()).date().isoformat()
    prefix = f"Akt-Kolaudimi_{safe_project}_{date_part}_v"
    escaped_folder = folder_id.replace("'", "\\'")
    files = (
        drive.files()
        .list(
            q=f"'{escaped_folder}' in parents and trashed = false",
            fields="files(name)",
            pageSize=1000,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
        .get("files", [])
    )
    versions = [
        int(match.group(1))
        for item in files
        if isinstance(item, dict)
        for match in [
            re.fullmatch(
                rf"{re.escape(prefix)}(\d+)\.pdf",
                str(item.get("name") or ""),
            )
        ]
        if match is not None
    ]
    return f"{prefix}{max(versions, default=0) + 1}.pdf"
