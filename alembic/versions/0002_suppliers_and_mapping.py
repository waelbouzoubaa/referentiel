"""Tables suppliers et mapping_rules.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMPTZ

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sage_supplier_code", sa.String()),
        sa.Column("sharepoint_folder", sa.String(), nullable=False),
        sa.Column("upload_mode", sa.String(), nullable=False, server_default="incremental"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("code", name="uq_suppliers_code"),
        sa.UniqueConstraint("sharepoint_folder", name="uq_suppliers_sharepoint_folder"),
        sa.CheckConstraint("upload_mode IN ('full', 'incremental')", name="chk_suppliers_upload_mode"),
    )
    op.create_index("idx_suppliers_active", "suppliers", ["active"])

    op.create_table(
        "mapping_rules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("supplier_id", sa.UUID(), sa.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column("yaml_hash", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("validated_by", sa.String()),
        sa.Column("validated_at", TIMESTAMPTZ()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("supplier_id", "version", name="uq_mapping_rules_supplier_version"),
        sa.UniqueConstraint("yaml_hash", name="uq_mapping_rules_yaml_hash"),
    )
    op.create_index("idx_mapping_rules_supplier", "mapping_rules", ["supplier_id"])
    # Index partiel : une seule version active par fournisseur
    op.execute(
        "CREATE UNIQUE INDEX uq_mapping_rules_one_active_per_supplier "
        "ON mapping_rules (supplier_id) WHERE active = TRUE"
    )


def downgrade() -> None:
    op.drop_table("mapping_rules")
    op.drop_table("suppliers")
