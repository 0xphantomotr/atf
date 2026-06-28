"""add prompt command runs

Revision ID: f4a9b2c7d1e6
Revises: e3c8a7d4f2b1
Create Date: 2026-06-28 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f4a9b2c7d1e6"
down_revision: str | None = "e3c8a7d4f2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_runs",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("review_job_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("original_prompt", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.String(length=64), nullable=True),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "planner_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "attachment_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pending_clarification",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confirmation_token_hash", sa.String(length=128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["review_job_id"], ["review_jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_prompt_runs_chat_message",
        ),
        sa.UniqueConstraint(
            "telegram_update_id",
            name="uq_prompt_runs_telegram_update_id",
        ),
    )
    op.create_index(
        "ix_prompt_runs_project_id",
        "prompt_runs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_runs_user_status",
        "prompt_runs",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "prompt_run_steps",
        sa.Column("prompt_run_id", sa.UUID(), nullable=False),
        sa.Column("step_key", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["prompt_run_id"], ["prompt_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prompt_run_id",
            "step_key",
            name="uq_prompt_run_steps_run_key",
        ),
    )
    op.create_index(
        "ix_prompt_run_steps_run_status",
        "prompt_run_steps",
        ["prompt_run_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prompt_run_steps_run_status",
        table_name="prompt_run_steps",
    )
    op.drop_table("prompt_run_steps")
    op.drop_index("ix_prompt_runs_user_status", table_name="prompt_runs")
    op.drop_index("ix_prompt_runs_project_id", table_name="prompt_runs")
    op.drop_table("prompt_runs")
