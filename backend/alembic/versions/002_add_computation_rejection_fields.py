"""Add computation rejection fields and REJECTED enum value.

Revision ID: 002
Revises: 001
Create Date: 2026-05-16
"""

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add REJECTED to computation_status enum
    op.execute("ALTER TYPE computation_status ADD VALUE IF NOT EXISTS 'REJECTED'")

    # Add COMPUTATION_REJECTED to audit_event_type enum
    op.execute("ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS 'COMPUTATION_REJECTED'")

    # Add rejection columns to filing_computations table
    op.add_column(
        "filing_computations",
        sa.Column("rejected_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "filing_computations",
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "filing_computations",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )

    # Add foreign key for rejected_by
    op.create_foreign_key(
        "fk_filing_computations_rejected_by",
        "filing_computations",
        "users",
        ["rejected_by"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_filing_computations_rejected_by", "filing_computations", type_="foreignkey")
    op.drop_column("filing_computations", "rejection_reason")
    op.drop_column("filing_computations", "rejected_at")
    op.drop_column("filing_computations", "rejected_by")
    # PostgreSQL does not support removing values from an enum type.
