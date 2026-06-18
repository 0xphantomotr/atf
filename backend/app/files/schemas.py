from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ProjectFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    original_filename: str
    normalized_filename: str
    mime_type: str
    current_version: int
    sha256_hash: str


class FileVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_id: UUID
    version_number: int
    original_filename: str
    sha256_hash: str
    mime_type: str
    size_bytes: int
    parse_status: str


class ProjectFileUploadRead(BaseModel):
    file: ProjectFileRead
    version: FileVersionRead


class ParsedDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_version_id: UUID
    document_type: str | None
    language: str
    page_count: int | None
    text_content: str | None
    document_metadata: dict
