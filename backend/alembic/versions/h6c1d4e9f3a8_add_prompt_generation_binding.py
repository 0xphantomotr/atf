"""add prompt generation binding

Revision ID: h6c1d4e9f3a8
Revises: g5b0c3d8e2f7
Create Date: 2026-06-29 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "h6c1d4e9f3a8"
down_revision: str | None = "g5b0c3d8e2f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prompt_runs",
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "review_jobs",
        sa.Column("prompt_run_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_review_jobs_prompt_run_id",
        "review_jobs",
        ["prompt_run_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_review_jobs_prompt_run_id",
        "review_jobs",
        type_="unique",
    )
    op.drop_column("review_jobs", "prompt_run_id")
    op.drop_column("prompt_runs", "confirmation_expires_at")
