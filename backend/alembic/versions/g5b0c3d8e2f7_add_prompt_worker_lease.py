"""add prompt worker lease

Revision ID: g5b0c3d8e2f7
Revises: f4a9b2c7d1e6
Create Date: 2026-06-28 19:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "g5b0c3d8e2f7"
down_revision: str | None = "f4a9b2c7d1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prompt_runs",
        sa.Column("worker_lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "prompt_runs",
        sa.Column(
            "worker_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("prompt_runs", "worker_attempt_count")
    op.drop_column("prompt_runs", "worker_lease_until")
