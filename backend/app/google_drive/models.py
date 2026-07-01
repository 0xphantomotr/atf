import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
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
