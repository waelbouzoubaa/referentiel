from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from middleware.api.schemas import SupplierOut
from middleware.parser.yaml_loader import load_all_mappings

router = APIRouter()


@router.get("", response_model=list[SupplierOut], summary="Liste des fournisseurs")
async def list_suppliers() -> list[SupplierOut]:
    """Liste tous les fournisseurs enregistrés (chargés depuis les YAMLs)."""
    config_dir = Path("config/suppliers")
    mappings = load_all_mappings(config_dir)
    return [
        SupplierOut(
            code=rule.supplier_code,
            name=rule.description or rule.supplier_code,
            active=True,
            upload_mode=rule.upload_mode,
            sharepoint_folder=rule.supplier_code,
        )
        for rule in mappings.values()
    ]
