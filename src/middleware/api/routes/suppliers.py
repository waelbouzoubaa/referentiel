from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("", summary="Liste des fournisseurs")
async def list_suppliers() -> dict[str, str]:
    """Liste tous les fournisseurs enregistrés. Implémenté au Livrable 2."""
    return {"message": "Livrable 2 — à implémenter"}
