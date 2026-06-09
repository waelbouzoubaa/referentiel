from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.api.schemas import AuditEntryOut, AuditResponse
from middleware.db.models import Product, ProductHistory, Supplier, SupplierFile
from middleware.db.session import get_session

router = APIRouter()

ChangeTypeFilter = Literal["CREATE", "UPDATE", "PRICE_CHANGE", "DELETE", "REACTIVATE"] | None


@router.get("/audit", response_model=AuditResponse, tags=["audit"])
async def get_audit(
    supplier_code: str | None = Query(None, description="Filtrer par fournisseur (ex: atlantic_scga_eau)"),
    change_type: ChangeTypeFilter = Query(None, description="Filtrer par type de changement"),
    since: datetime | None = Query(None, description="Depuis (ISO 8601, ex: 2026-06-09T00:00:00)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AuditResponse:
    """Journal d'audit des changements produits : quelle ligne a changé, quand et comment."""

    base = (
        select(
            ProductHistory.id,
            ProductHistory.detected_at,
            ProductHistory.change_type,
            ProductHistory.field_changes,
            Product.supplier_product_code,
            Product.designation,
            Supplier.code.label("supplier_code"),
            SupplierFile.filename,
        )
        .join(Product, ProductHistory.product_id == Product.id)
        .join(Supplier, Product.supplier_id == Supplier.id)
        .join(SupplierFile, ProductHistory.source_file_id == SupplierFile.id)
    )

    if supplier_code:
        base = base.where(Supplier.code == supplier_code)
    if change_type:
        base = base.where(ProductHistory.change_type == change_type)
    if since:
        base = base.where(ProductHistory.detected_at >= since)

    count_q = select(func.count()).select_from(base.subquery())
    total: int = (await session.execute(count_q)).scalar_one()

    rows = (
        await session.execute(
            base.order_by(ProductHistory.detected_at.desc()).limit(limit).offset(offset)
        )
    ).all()

    entries = [
        AuditEntryOut(
            id=row.id,
            detected_at=row.detected_at,
            supplier_code=row.supplier_code,
            supplier_product_code=row.supplier_product_code,
            designation=row.designation,
            change_type=row.change_type,
            source_file=row.filename,
            field_changes=row.field_changes,
        )
        for row in rows
    ]

    return AuditResponse(total=total, limit=limit, offset=offset, entries=entries)
