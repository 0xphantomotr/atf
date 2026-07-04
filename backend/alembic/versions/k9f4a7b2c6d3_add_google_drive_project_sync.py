"""add google drive project sync

Revision ID: k9f4a7b2c6d3
Revises: j8e3f6a1b5c2
Create Date: 2026-07-03 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "k9f4a7b2c6d3"
down_revision: str | None = "j8e3f6a1b5c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "google_drive_project_bindings",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.String(length=200), nullable=False),
        sa.Column("folder_name", sa.String(length=512), nullable=False),
        sa.Column("folder_url", sa.Text(), nullable=False),
        sa.Column("output_folder_id", sa.String(length=200), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=32),
            server_default="never_synced",
            nullable=False,
        ),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_sync_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            name="uq_google_drive_project_bindings_project_id",
        ),
        sa.UniqueConstraint(
            "user_id",
            "folder_id",
            name="uq_google_drive_project_bindings_user_folder",
        ),
    )
    op.create_table(
        "google_drive_file_syncs",
        sa.Column("binding_id", sa.Uuid(), nullable=False),
        sa.Column("drive_file_id", sa.String(length=200), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("md5_checksum", sa.String(length=64), nullable=True),
        sa.Column("drive_version", sa.String(length=64), nullable=True),
        sa.Column("project_file_id", sa.Uuid(), nullable=True),
        sa.Column("file_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(["binding_id"], ["google_drive_project_bindings.id"]),
        sa.ForeignKeyConstraint(["file_version_id"], ["file_versions.id"]),
        sa.ForeignKeyConstraint(["project_file_id"], ["project_files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "binding_id",
            "drive_file_id",
            name="uq_google_drive_file_syncs_binding_file",
        ),
    )
    op.create_index(
        "ix_google_drive_file_syncs_binding_status",
        "google_drive_file_syncs",
        ["binding_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_google_drive_file_syncs_binding_status",
        table_name="google_drive_file_syncs",
    )
    op.drop_table("google_drive_file_syncs")
    op.drop_table("google_drive_project_bindings")
