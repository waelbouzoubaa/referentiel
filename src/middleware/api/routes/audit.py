from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.api.schemas import AuditEntryOut, AuditResponse
from middleware.db.models import Product, ProductAudit, Supplier, SupplierFile
from middleware.db.session import get_session

router = APIRouter()

ChangeTypeFilter = Literal["designation", "family", "subfamily", "price"] | None


@router.get("/audit", response_model=AuditResponse, tags=["audit"])
async def get_audit(
    supplier_code: str | None = Query(None, description="Filtrer par fournisseur (ex: atlantic_scga_eau)"),
    field_name: str | None = Query(None, description="Filtrer par champ modifié (ex: designation, price, attr_epaisseur)"),
    since: datetime | None = Query(None, description="Depuis (ISO 8601, ex: 2026-06-09T00:00:00)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AuditResponse:
    """Journal d'audit : quel champ a changé, sur quel produit, quand et depuis quel fichier."""

    base = (
        select(
            ProductAudit.id,
            ProductAudit.changed_at,
            ProductAudit.field_name,
            Product.supplier_product_code,
            Product.designation,
            Supplier.code.label("supplier_code"),
            SupplierFile.filename,
        )
        .join(Product, ProductAudit.product_id == Product.id)
        .join(Supplier, Product.supplier_id == Supplier.id)
        .join(SupplierFile, ProductAudit.source_file_id == SupplierFile.id)
    )

    if supplier_code:
        base = base.where(Supplier.code == supplier_code)
    if field_name:
        base = base.where(ProductAudit.field_name == field_name)
    if since:
        base = base.where(ProductAudit.changed_at >= since)

    total: int = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar_one()

    rows = (
        await session.execute(
            base.order_by(ProductAudit.changed_at.desc()).limit(limit).offset(offset)
        )
    ).all()

    entries = [
        AuditEntryOut(
            id=row.id,
            changed_at=row.changed_at,
            supplier_code=row.supplier_code,
            supplier_product_code=row.supplier_product_code,
            designation=row.designation,
            field_name=row.field_name,
            source_file=row.filename,
        )
        for row in rows
    ]

    return AuditResponse(total=total, limit=limit, offset=offset, entries=entries)
