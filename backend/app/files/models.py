import uuid

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import Vector


class ProjectFile(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "project_files"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class FileVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "file_versions"

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_files.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class ParsedDocument(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "parsed_documents"

    file_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_versions.id"), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="sq-AL", nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class DocumentChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "document_chunks"

    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parsed_documents.id"), nullable=False
    )
    file_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_versions.id"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

