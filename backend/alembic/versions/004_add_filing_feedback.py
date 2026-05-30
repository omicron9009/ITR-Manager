"""Add filing_feedback table.

Revision ID: 004
Revises: 003
Create Date: 2026-05-31
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "filing_feedback",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filing_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("itr_filings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("filing_id", name="uq_filing_feedback_filing_id"),
    )


def downgrade() -> None:
    op.drop_table("filing_feedback")
