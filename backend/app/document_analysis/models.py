import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentAnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_analysis_runs"
    __table_args__ = (
        Index("ix_document_analysis_runs_cache_status", "cache_key", "status"),
        Index("ix_document_analysis_runs_project_id", "project_id"),
    )

    file_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_versions.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    batch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_batch_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    document_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentAnalysisBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_analysis_batches"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "batch_index",
            name="uq_document_analysis_batches_run_index",
        ),
    )

    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_analysis_runs.id"), nullable=False
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    token_usage: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentAnalysisClaim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "document_analysis_claims"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "claim_index",
            name="uq_document_analysis_claims_run_index",
        ),
        Index("ix_document_analysis_claims_project_field", "project_id", "field_name"),
    )

    analysis_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_analysis_runs.id"), nullable=False
    )
    file_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("file_versions.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    claim_index: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    evidence: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    extraction_method: Mapped[str] = mapped_column(
        String(64), default="ai_chunk_analysis", nullable=False
    )
    claim_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
