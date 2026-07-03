import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class GoogleDriveConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "google_drive_connections"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_google_drive_connections_user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    google_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    google_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[str] = mapped_column(Text, nullable=False)


class GoogleOAuthState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "google_oauth_states"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_google_oauth_states_token_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GoogleDriveProjectBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "google_drive_project_bindings"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_google_drive_project_bindings_project_id"),
        UniqueConstraint(
            "user_id",
            "folder_id",
            name="uq_google_drive_project_bindings_user_folder",
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    folder_id: Mapped[str] = mapped_column(String(200), nullable=False)
    folder_name: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_url: Mapped[str] = mapped_column(Text, nullable=False)
    output_folder_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sync_status: Mapped[str] = mapped_column(String(32), default="never_synced", nullable=False)
    last_sync_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_sync_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class GoogleDriveFileSync(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "google_drive_file_syncs"
    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "drive_file_id",
            name="uq_google_drive_file_syncs_binding_file",
        ),
        Index("ix_google_drive_file_syncs_binding_status", "binding_id", "status"),
    )

    binding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("google_drive_project_bindings.id"),
        nullable=False,
    )
    drive_file_id: Mapped[str] = mapped_column(String(200), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    modified_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    md5_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    drive_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_files.id"),
        nullable=True,
    )
    file_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("file_versions.id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
