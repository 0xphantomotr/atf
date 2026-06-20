"""add user ai settings

Revision ID: df4b6bb7f6b0
Revises: 9a4d7b2d5639
Create Date: 2026-06-19 15:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "df4b6bb7f6b0"
down_revision: str | None = "9a4d7b2d5639"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_ai_settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("selected_model", sa.String(length=255), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_ai_settings_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_ai_settings")),
        sa.UniqueConstraint("user_id", name="uq_user_ai_settings_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_ai_settings")
