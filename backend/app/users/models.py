import uuid

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="sq-AL", nullable=False)

    telegram_accounts: Mapped[list["TelegramAccount"]] = relationship(back_populates="user")


class TelegramAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telegram_accounts"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_telegram_accounts_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    active_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id"),
        nullable=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="telegram_accounts")
