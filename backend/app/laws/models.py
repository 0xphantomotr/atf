import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import Vector


class LawDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "law_documents"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="sq-AL", nullable=False)
    version_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LawArticle(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "law_articles"

    law_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("law_documents.id"), nullable=False)
    chapter: Mapped[str | None] = mapped_column(String(128), nullable=True)
    article_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    article_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class LawChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "law_chunks"

    law_article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("law_articles.id"), nullable=False)
    law_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("law_documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

