"""add document chunk uniqueness

Revision ID: a51f3c9d72e4
Revises: 6b1df2e9a8c4
Create Date: 2026-06-21 10:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a51f3c9d72e4"
down_revision: str | None = "6b1df2e9a8c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_parsed_documents_file_version_id",
        "parsed_documents",
        ["file_version_id"],
    )
    op.create_unique_constraint(
        "uq_document_chunks_file_version_chunk_index",
        "document_chunks",
        ["file_version_id", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_document_chunks_file_version_chunk_index",
        "document_chunks",
        type_="unique",
    )
    op.drop_constraint(
        "uq_parsed_documents_file_version_id",
        "parsed_documents",
        type_="unique",
    )
