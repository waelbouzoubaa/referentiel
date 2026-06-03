"""Tables products, product_variants, product_attributes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("supplier_id", sa.UUID(), sa.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("supplier_product_code", sa.String(), nullable=False),
        sa.Column("generic_code", sa.String()),
        sa.Column("designation", sa.String(), nullable=False),
        sa.Column("family", sa.String()),
        sa.Column("subfamily", sa.String()),
        sa.Column("product_kind", sa.String(), nullable=False, server_default="physical"),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("first_seen_in_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="SET NULL")),
        sa.Column("last_seen_in_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="SET NULL")),
        sa.Column("business_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("supplier_id", "supplier_product_code", name="uq_products_supplier_code"),
        sa.CheckConstraint("product_kind IN ('physical','service')", name="chk_products_kind"),
        sa.CheckConstraint("status IN ('active','inactive','deleted')", name="chk_products_status"),
    )
    op.create_index("idx_products_supplier", "products", ["supplier_id"])
    op.create_index("idx_products_business_hash", "products", ["business_hash"])
    # Index trigram pour recherche fuzzy sur designation
    op.execute(
        "CREATE INDEX idx_products_designation_trgm ON products "
        "USING gin (designation gin_trgm_ops)"
    )

    op.create_table(
        "product_variants",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant_dimension", sa.String(), nullable=False),
        sa.Column("variant_value", sa.String(), nullable=False),
        sa.Column("variant_code", sa.String(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint(
            "product_id", "variant_dimension", "variant_code",
            name="uq_product_variants_product_dim_val",
        ),
    )
    op.create_index("idx_product_variants_product", "product_variants", ["product_id"])

    op.create_table(
        "product_attributes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("product_id", sa.UUID(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attribute_key", sa.String(), nullable=False),
        sa.Column("attribute_value", sa.Text(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("unit", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("product_id", "attribute_key", name="uq_product_attributes_product_key"),
        sa.CheckConstraint(
            "data_type IN ('string','integer','decimal','enum','duration','boolean')",
            name="chk_product_attributes_type",
        ),
    )
    op.create_index("idx_product_attributes_product", "product_attributes", ["product_id"])


def downgrade() -> None:
    op.drop_table("product_attributes")
    op.drop_table("product_variants")
    op.drop_table("products")
