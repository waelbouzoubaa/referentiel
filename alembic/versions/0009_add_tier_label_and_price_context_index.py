"""Ajoute tier_label sur prices + index d'unicité uq_prices_context.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prices", sa.Column("tier_label", sa.String(), nullable=True))

    # Index unique de contexte tarifaire (idempotence sur ré-ingestion)
    op.execute("""
        CREATE UNIQUE INDEX uq_prices_context ON prices (
            product_id,
            COALESCE(variant_id::TEXT, ''),
            price_type,
            COALESCE(tier_min_quantity::TEXT, ''),
            COALESCE(tier_max_quantity::TEXT, ''),
            COALESCE(valid_from::TEXT, '')
        )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_prices_context")
    op.drop_column("prices", "tier_label")
