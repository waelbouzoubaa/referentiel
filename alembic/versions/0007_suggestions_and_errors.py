"""Tables mapping_suggestions et processing_errors.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mapping_suggestions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("supplier_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suggested_yaml", sa.Text(), nullable=False),
        sa.Column("confidence_avg", sa.Numeric(5, 4), nullable=False),
        sa.Column("field_confidences", JSONB(), nullable=False),
        sa.Column("warnings", JSONB()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String()),
        sa.Column("reviewed_at", TIMESTAMPTZ()),
        sa.Column("approved_yaml", sa.Text()),
        sa.Column("resulting_mapping_rule_id", sa.UUID(), sa.ForeignKey("mapping_rules.id", ondelete="SET NULL")),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','modified')",
            name="chk_mapping_suggestions_status",
        ),
        sa.CheckConstraint(
            "confidence_avg >= 0 AND confidence_avg <= 1",
            name="chk_mapping_suggestions_confidence",
        ),
    )
    op.create_index("idx_mapping_suggestions_status", "mapping_suggestions", ["status"])
    op.create_index("idx_mapping_suggestions_file", "mapping_suggestions", ["supplier_file_id"])

    op.create_table(
        "processing_errors",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("supplier_file_id", sa.UUID(), sa.ForeignKey("supplier_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_number", sa.Integer()),
        sa.Column("error_type", sa.String(), nullable=False),
        sa.Column("error_field", sa.String()),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("raw_value", sa.Text()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "error_type IN ('parse_error','validation_error','mapping_error','transform_error')",
            name="chk_processing_errors_type",
        ),
    )
    op.create_index("idx_processing_errors_file", "processing_errors", ["supplier_file_id"])
    op.create_index("idx_processing_errors_type", "processing_errors", ["error_type"])


def downgrade() -> None:
    op.drop_table("processing_errors")
    op.drop_table("mapping_suggestions")
