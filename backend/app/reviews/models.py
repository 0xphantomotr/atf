import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ReviewJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="sq-AL", nullable=False)
    output_format: Mapped[str] = mapped_column(String(32), nullable=False)
    user_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    law_scope: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_findings"

    review_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_jobs.id"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    law_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    required_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)


class GeneratedOutput(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "generated_outputs"

    review_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_jobs.id"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="sq-AL", nullable=False)
    storage_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)

