"""Tables prices et commercial_rules.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prices",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_id", sa.UUID(), sa.ForeignKey("product_variants.id", ondelete="CASCADE")),
        sa.Column("price_type", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(15, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("tier_min_quantity", sa.Numeric(15, 4)),
        sa.Column("tier_max_quantity", sa.Numeric(15, 4)),
        sa.Column("tier_unit", sa.String()),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.Column("source_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint("amount >= 0", name="chk_prices_amount_positive"),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="chk_prices_validity",
        ),
    )
    op.create_index("idx_prices_product", "prices", ["product_id"])
    op.create_index("idx_prices_variant", "prices", ["variant_id"])
    op.create_index("idx_prices_type", "prices", ["price_type"])

    op.create_table(
        "commercial_rules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="CASCADE")),
        sa.Column("supplier_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="CASCADE")),
        sa.Column("rule_type", sa.String(), nullable=False),
        sa.Column("threshold_value", sa.Numeric(15, 4)),
        sa.Column("threshold_unit", sa.String()),
        sa.Column("description", sa.Text()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_commercial_rules_product", "commercial_rules", ["product_id"])


def downgrade() -> None:
    op.drop_table("commercial_rules")
    op.drop_table("prices")
