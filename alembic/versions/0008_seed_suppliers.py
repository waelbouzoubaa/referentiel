"""Seed des 4 fournisseurs pilotes (tous inactifs au démarrage).

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-02
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

SUPPLIERS = [
    {
        "id": str(uuid.uuid4()),
        "code": "atlantic_scga_chauffage",
        "name": "Atlantic SCGA — Chauffage électrique",
        "sharepoint_folder": "/ATLANTIC (SCGA)",
        "upload_mode": "full",
        "active": False,
    },
    {
        "id": str(uuid.uuid4()),
        "code": "atlantic_scga_eau",
        "name": "Atlantic SCGA — Eau chaude sanitaire",
        "sharepoint_folder": "/ATLANTIC (SCGA)/eau",
        "upload_mode": "full",
        "active": False,
    },
    {
        "id": str(uuid.uuid4()),
        "code": "airisol",
        "name": "Airisol",
        "sharepoint_folder": "/AIRISOL",
        "upload_mode": "full",
        "active": False,
    },
    {
        "id": str(uuid.uuid4()),
        "code": "agenor",
        "name": "Agenor",
        "sharepoint_folder": "/AGENOR",
        "upload_mode": "full",
        "active": False,
    },
]


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
            "suppliers",
            sa.column("id", sa.UUID()),
            sa.column("code", sa.String()),
            sa.column("name", sa.String()),
            sa.column("sharepoint_folder", sa.String()),
            sa.column("upload_mode", sa.String()),
            sa.column("active", sa.Boolean()),
        ),
        SUPPLIERS,
    )


def downgrade() -> None:
    codes = [s["code"] for s in SUPPLIERS]
    op.execute(
        sa.text("DELETE FROM suppliers WHERE code = ANY(:codes)").bindparams(codes=codes)
    )
