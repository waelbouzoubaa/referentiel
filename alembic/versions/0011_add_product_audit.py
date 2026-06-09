"""Remplace field_changes (hashes illisibles) par product_audit (champ par champ).

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("product_history", "field_changes")

    op.create_table(
        "product_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("supplier_files.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("idx_product_audit_product_id", "product_audit", ["product_id"])
    op.create_index("idx_product_audit_changed_at", "product_audit", ["changed_at"])
    op.create_index("idx_product_audit_field_name", "product_audit", ["field_name"])


def downgrade() -> None:
    op.drop_table("product_audit")
    op.add_column(
        "product_history",
        sa.Column("field_changes", postgresql.JSONB, nullable=True),
    )
