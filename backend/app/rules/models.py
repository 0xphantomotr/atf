import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Rule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rules"

    rule_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    law_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("law_documents.id"), nullable=False)
    law_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("law_articles.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    applies_to: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    required_documents: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    required_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    severity_if_missing: Mapped[str] = mapped_column(String(32), nullable=False)
    human_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

