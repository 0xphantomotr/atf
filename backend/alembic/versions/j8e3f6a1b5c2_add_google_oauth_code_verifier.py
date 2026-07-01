"""add google oauth code verifier

Revision ID: j8e3f6a1b5c2
Revises: i7d2e5f0a4b9
Create Date: 2026-06-30 19:10:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "j8e3f6a1b5c2"
down_revision: str | None = "i7d2e5f0a4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM google_oauth_states")
    op.add_column(
        "google_oauth_states",
        sa.Column("encrypted_code_verifier", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("google_oauth_states", "encrypted_code_verifier")
