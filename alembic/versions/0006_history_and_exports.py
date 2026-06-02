"""Tables product_history, gery_exports, gery_export_lines.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gery_exports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("export_kind", sa.String(), nullable=False),
        sa.Column("generated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("delivered_at", TIMESTAMPTZ()),
        sa.Column("output_path", sa.String(), nullable=False),
        sa.Column("output_hash", sa.String(64), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="generated"),
        sa.Column("ack_message", sa.Text()),
        sa.CheckConstraint(
            "export_kind IN ('NEW_ARTICLE','NEW_ART_FRNS_CREATE','NEW_ART_FRNS_PRICE_UPDATE')",
            name="chk_gery_exports_kind",
        ),
        sa.CheckConstraint(
            "status IN ('generated','delivered','acknowledged','failed')",
            name="chk_gery_exports_status",
        ),
    )
    op.create_index("idx_gery_exports_kind", "gery_exports", ["export_kind"])
    op.create_index("idx_gery_exports_status", "gery_exports", ["status"])

    op.create_table(
        "product_history",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("change_type", sa.String(), nullable=False),
        sa.Column("field_changes", JSONB()),
        sa.Column("source_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("detected_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("exported_at", TIMESTAMPTZ()),
        sa.Column("exported_in_id", sa.UUID(), sa.ForeignKey("gery_exports.id", ondelete="SET NULL")),
        sa.CheckConstraint(
            "change_type IN ('CREATE','UPDATE','PRICE_CHANGE','DELETE','REACTIVATE')",
            name="chk_product_history_type",
        ),
    )
    op.create_index("idx_product_history_product", "product_history", ["product_id", "detected_at"])
    op.execute(
        "CREATE INDEX idx_product_history_unexported ON product_history (exported_at) "
        "WHERE exported_at IS NULL"
    )

    op.create_table(
        "gery_export_lines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("export_id", sa.UUID(), sa.ForeignKey("gery_exports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("variant_id", sa.UUID(), sa.ForeignKey("product_variants.id", ondelete="RESTRICT")),
        sa.Column("price_id", sa.UUID(), sa.ForeignKey("prices.id", ondelete="RESTRICT")),
        sa.Column("derived_code", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.UniqueConstraint("export_id", "line_number", name="uq_gery_export_lines_export_line"),
    )
    op.create_index("idx_gery_export_lines_export", "gery_export_lines", ["export_id"])
    op.create_index("idx_gery_export_lines_product", "gery_export_lines", ["product_id"])


def downgrade() -> None:
    op.drop_table("gery_export_lines")
    op.drop_table("product_history")
    op.drop_table("gery_exports")
