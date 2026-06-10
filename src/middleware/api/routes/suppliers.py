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
            sharepoint_folder=rule.resolved_sharepoint_folders()[0],
        )
        for rule in mappings.values()
    ]


@router.get(
    "/folder-mapping",
    response_model=dict[str, str],
    summary="Mapping dossier SharePoint (minuscules) -> supplier_code",
)
async def folder_mapping() -> dict[str, str]:
    """Mapping consommé par le watcher pour résoudre le fournisseur d'un fichier déposé.

    Construit dynamiquement depuis `sharepoint_folder` de chaque YAML — ajouter un
    fournisseur ne nécessite donc aucune modification du watcher.
    """
    config_dir = Path("config/suppliers")
    mappings = load_all_mappings(config_dir)
    result: dict[str, str] = {}
    for rule in mappings.values():
        for folder in rule.resolved_sharepoint_folders():
            result[folder.lower()] = rule.supplier_code
    return result
