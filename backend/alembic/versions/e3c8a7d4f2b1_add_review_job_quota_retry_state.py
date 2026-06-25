"""add review job quota retry state

Revision ID: e3c8a7d4f2b1
Revises: c84d7e1a2f30
Create Date: 2026-06-25 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e3c8a7d4f2b1"
down_revision: str | None = "c84d7e1a2f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_jobs",
        sa.Column("current_stage", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "review_jobs",
        sa.Column(
            "progress_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "review_jobs",
        sa.Column("retry_after_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_jobs",
        sa.Column("retry_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "review_jobs",
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("review_jobs", "retry_count")
    op.drop_column("review_jobs", "retry_reason")
    op.drop_column("review_jobs", "retry_after_at")
    op.drop_column("review_jobs", "progress_details")
    op.drop_column("review_jobs", "current_stage")
