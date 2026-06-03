from __future__ import annotations

from fastapi import APIRouter, HTTPException

from middleware.api.schemas import ProductHistoryResponse
from middleware.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/{supplier_code}/{product_code}/history", response_model=ProductHistoryResponse, tags=["produits"])
async def get_product_history(supplier_code: str, product_code: str) -> ProductHistoryResponse:
    """Retourne l'historique des changements détectés pour un produit.

    En production, cet endpoint interroge la table product_history en base.
    Pour l'instant, retourne un historique vide (DB non connectée dans ce stub).

    Args:
        supplier_code: Code fournisseur (ex: atlantic_scga_chauffage).
        product_code: Référence article fournisseur.

    Returns:
        Historique des changements (CREATE/UPDATE/PRICE_CHANGE/DELETE/REACTIVATE).
    """
    # Stub : en production, requête async sur product_history WHERE supplier+code
    logger.info(
        "historique produit demandé",
        supplier_code=supplier_code,
        product_code=product_code,
    )
    return ProductHistoryResponse(
        supplier_product_code=product_code,
        supplier_code=supplier_code,
        history=[],
    )
