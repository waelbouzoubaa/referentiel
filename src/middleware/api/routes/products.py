from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.api.schemas import ProductHistoryEntry, ProductHistoryResponse
from middleware.core.logging import get_logger
from middleware.db.models import Product, ProductAudit, ProductHistory, Supplier
from middleware.db.session import get_session

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/{supplier_code}/{product_code}/history",
    response_model=ProductHistoryResponse,
    tags=["produits"],
)
async def get_product_history(
    supplier_code: str,
    product_code: str,
    session: AsyncSession = Depends(get_session),
) -> ProductHistoryResponse:
    """Retourne l'historique des changements détectés pour un produit.

    Args:
        supplier_code: Code fournisseur (ex: atlantic_scga_chauffage).
        product_code: Référence article fournisseur.

    Returns:
        Historique des changements (CREATE/UPDATE/PRICE_CHANGE/DELETE/REACTIVATE).
    """
    product_id = (
        await session.execute(
            select(Product.id)
            .join(Supplier, Product.supplier_id == Supplier.id)
            .where(Supplier.code == supplier_code, Product.supplier_product_code == product_code)
        )
    ).scalar_one_or_none()

    if product_id is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")

    history_rows = (
        await session.execute(
            select(ProductHistory)
            .where(ProductHistory.product_id == product_id)
            .order_by(ProductHistory.detected_at.desc())
        )
    ).scalars().all()

    audit_rows = (
        await session.execute(
            select(ProductAudit.source_file_id, ProductAudit.field_name)
            .where(ProductAudit.product_id == product_id)
        )
    ).all()
    fields_by_file: dict[uuid.UUID, list[str]] = {}
    for source_file_id, field_name in audit_rows:
        fields_by_file.setdefault(source_file_id, []).append(field_name)

    history = [
        ProductHistoryEntry(
            change_type=row.change_type,
            field_changes={"changed_fields": fields_by_file[row.source_file_id]}
            if row.source_file_id in fields_by_file
            else None,
            detected_at=row.detected_at,
            exported_at=row.exported_at,
        )
        for row in history_rows
    ]

    return ProductHistoryResponse(
        supplier_product_code=product_code,
        supplier_code=supplier_code,
        history=history,
    )
