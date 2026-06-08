"""Ajoute business_hash_no_prices sur products (distinction UPDATE / PRICE_CHANGE).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("business_hash_no_prices", sa.String(length=64), nullable=False, server_default=""),
    )
    op.alter_column("products", "business_hash_no_prices", server_default=None)


def downgrade() -> None:
    op.drop_column("products", "business_hash_no_prices")
