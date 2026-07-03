from datetime import datetime, timezone
from uuid import uuid4

from app.google_drive.metadata import (
    FOLDER_MIME_TYPE,
    DriveFileRef,
    drive_ref_changed,
    list_folder_tree,
    next_output_filename,
    preflight_counts,
    supported_ref,
)


class _Request:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self) -> dict:
        return self.payload


class _Files:
    def __init__(self, children: dict[str, list[dict]], names: list[str] | None = None) -> None:
        self.children = children
        self.names = names or []

    def get(self, *, fileId: str, **_: object) -> _Request:
        return _Request(
            {
                "id": fileId,
                "name": "Dosja Teknike",
                "mimeType": FOLDER_MIME_TYPE,
                "parents": [],
                "capabilities": {"canAddChildren": True},
            }
        )

    def list(self, *, q: str, **_: object) -> _Request:
        for parent_id, items in self.children.items():
            if f"'{parent_id}' in parents" in q:
                return _Request({"files": items})
        return _Request({"files": [{"name": name} for name in self.names]})


class _Drive:
    def __init__(self, children: dict[str, list[dict]], names: list[str] | None = None) -> None:
        self._files = _Files(children, names)

    def files(self) -> _Files:
        return self._files


def _ref(*, version: str = "1", path: str = "Dosja Teknike/Akti.docx") -> DriveFileRef:
    return DriveFileRef(
        file_id="drive-file-123",
        relative_path=path,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=1200,
        modified_time=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        md5_checksum="a" * 32,
        drive_version=version,
    )


class _Manifest:
    def __init__(self, *, version: str = "1") -> None:
        self.drive_file_id = "drive-file-123"
        self.size_bytes = 1200
        self.modified_time = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
        self.md5_checksum = "a" * 32
        self.drive_version = version
        self.file_version_id = uuid4()
        self.status = "active"


def _manifest(*, version: str = "1") -> _Manifest:
    return _Manifest(version=version)


def test_drive_metadata_detects_only_real_content_changes() -> None:
    row = _manifest()

    assert drive_ref_changed(row, _ref()) is False
    assert drive_ref_changed(row, _ref(path="Dosja Teknike/Riemertuar.docx")) is False
    assert drive_ref_changed(row, _ref(version="2")) is True


def test_preflight_counts_new_changed_and_unchanged_files() -> None:
    unchanged = _ref()
    changed = DriveFileRef(**{**_ref(version="2").__dict__, "file_id": "changed"})
    new = DriveFileRef(**{**_ref().__dict__, "file_id": "new"})
    unsupported = DriveFileRef(
        **{
            **_ref().__dict__,
            "file_id": "unsupported",
            "relative_path": "Dosja Teknike/Skedar.tmp",
            "mime_type": "application/octet-stream",
        }
    )
    changed_row = _manifest(version="1")
    changed_row.drive_file_id = "changed"
    manifest = {"drive-file-123": _manifest(), "changed": changed_row}

    counts, skipped = preflight_counts(
        [unchanged, changed, new, unsupported],
        manifest,
    )

    assert counts == {"new": 1, "changed": 1, "unchanged": 1}
    assert skipped == [
        {"filename": "Dosja Teknike/Skedar.tmp", "reason": "format i pambështetur"}
    ]


def test_folder_listing_is_recursive_and_excludes_managed_outputs() -> None:
    drive = _Drive(
        {
            "root-folder": [
                {
                    "id": "sub-folder",
                    "name": "Nenfolder",
                    "mimeType": FOLDER_MIME_TYPE,
                },
                {
                    "id": "output-folder",
                    "name": "Kolaudimi",
                    "mimeType": FOLDER_MIME_TYPE,
                    "appProperties": {"atf_output_folder": "true"},
                },
                {
                    "id": "generated-pdf",
                    "name": "akt-kolaudimi.pdf",
                    "mimeType": "application/pdf",
                    "appProperties": {"atf_review_job_id": "review-1"},
                },
                {
                    "id": "root-doc",
                    "name": "Kontrata.docx",
                    "mimeType": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                    "size": "100",
                    "version": "1",
                },
            ],
            "sub-folder": [
                {
                    "id": "nested-doc",
                    "name": "Procesverbali.pdf",
                    "mimeType": "application/pdf",
                    "size": "200",
                    "version": "3",
                }
            ],
        }
    )

    _, refs = list_folder_tree(drive, "root-folder", max_files=250)

    assert [ref.file_id for ref in refs] == ["root-doc", "nested-doc"]
    assert refs[1].relative_path == "Dosja Teknike/Nenfolder/Procesverbali.pdf"


def test_output_filename_uses_next_daily_version() -> None:
    drive = _Drive(
        {},
        names=[
            "Akt-Kolaudimi_Test-Projekt_2026-07-03_v1.pdf",
            "Akt-Kolaudimi_Test-Projekt_2026-07-03_v3.pdf",
            "tjeter.pdf",
        ],
    )

    filename = next_output_filename(
        drive,
        folder_id="output-folder",
        project_name="Test Projekt",
        generated_at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
    )

    assert filename.endswith("_v4.pdf")


def test_google_native_document_is_exported_to_docx() -> None:
    ref = DriveFileRef(
        file_id="google-doc-123",
        relative_path="Dosja Teknike/Raporti",
        mime_type="application/vnd.google-apps.document",
        size_bytes=None,
        modified_time=None,
        md5_checksum=None,
        drive_version="4",
    )

    output_name, output_mime, reason = supported_ref(ref)

    assert output_name == "Dosja Teknike/Raporti.docx"
    assert output_mime.endswith("wordprocessingml.document")
    assert reason is None
