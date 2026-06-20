"""add active telegram project

Revision ID: 6b1df2e9a8c4
Revises: df4b6bb7f6b0
Create Date: 2026-06-20 16:10:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "6b1df2e9a8c4"
down_revision: str | None = "df4b6bb7f6b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_accounts",
        sa.Column("active_project_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_telegram_accounts_active_project_id_projects"),
        "telegram_accounts",
        "projects",
        ["active_project_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_telegram_accounts_active_project_id_projects"),
        "telegram_accounts",
        type_="foreignkey",
    )
    op.drop_column("telegram_accounts", "active_project_id")
