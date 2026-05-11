"""Add ITR_JSON to completed_doc_type enum.

Revision ID: 001
Revises:
Create Date: 2026-05-10
"""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE completed_doc_type ADD VALUE IF NOT EXISTS 'ITR_JSON'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # To fully revert, you would need to recreate the enum and migrate the column.
    pass
