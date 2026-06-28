import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PromptRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_runs"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_prompt_runs_chat_message",
        ),
        Index("ix_prompt_runs_user_status", "user_id", "status"),
        Index("ix_prompt_runs_project_id", "project_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_update_id: Mapped[int | None] = mapped_column(
        BigInteger,
        unique=True,
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True,
    )
    review_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_jobs.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(48), default="planning", nullable=False)
    original_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    plan_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    planner_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    attachment_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    pending_clarification: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confirmation_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class PromptRunStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "prompt_run_steps"
    __table_args__ = (
        UniqueConstraint(
            "prompt_run_id",
            "step_key",
            name="uq_prompt_run_steps_run_key",
        ),
        Index("ix_prompt_run_steps_run_status", "prompt_run_id", "status"),
    )

    prompt_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_runs.id"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    result_data: Mapped[dict] = mapped_column("result", JSONB, default=dict, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
