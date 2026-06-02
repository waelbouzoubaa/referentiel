"""Table supplier_files.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_files",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("supplier_id", sa.UUID(), sa.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("sharepoint_item_id", sa.String(), nullable=False),
        sa.Column("sharepoint_etag", sa.String()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("gcs_path", sa.String(), nullable=False),
        sa.Column("received_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("processing_started_at", TIMESTAMPTZ()),
        sa.Column("processing_ended_at", TIMESTAMPTZ()),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("error_message", sa.Text()),
        sa.Column("validity_start", sa.Date()),
        sa.Column("validity_end", sa.Date()),
        sa.Column("contract_reference", sa.String()),
        sa.Column("geographic_scope", sa.String()),
        sa.Column("organizational_scope", sa.String()),
        sa.Column("mapping_rule_id", sa.UUID(), sa.ForeignKey("mapping_rules.id", ondelete="RESTRICT")),
        sa.Column("raw_metadata", JSONB()),
        sa.UniqueConstraint("content_hash", name="uq_supplier_files_hash"),
        sa.CheckConstraint(
            "status IN ('received','processing','processed','failed','skipped')",
            name="chk_supplier_files_status",
        ),
    )
    op.create_index("idx_supplier_files_supplier", "supplier_files", ["supplier_id"])
    op.create_index("idx_supplier_files_status", "supplier_files", ["status"])
    op.create_index("idx_supplier_files_sharepoint_item_id", "supplier_files", ["sharepoint_item_id"])


def downgrade() -> None:
    op.drop_table("supplier_files")
