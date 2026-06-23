"""add ai stage models and execution plan

Revision ID: c84d7e1a2f30
Revises: b72e8c4f19a6
Create Date: 2026-06-22 20:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c84d7e1a2f30"
down_revision: str | None = "b72e8c4f19a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_ai_settings",
        sa.Column(
            "stage_models",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "review_jobs",
        sa.Column(
            "execution_plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("review_jobs", "execution_plan")
    op.drop_column("user_ai_settings", "stage_models")
