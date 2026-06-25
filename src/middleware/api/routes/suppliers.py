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
    summary="Mapping dossier SharePoint -> liste de YAMLs avec leurs mots-clés fichier",
)
async def folder_mapping() -> dict[str, list[dict]]:
    """Mapping consommé par le watcher pour résoudre quel YAML appliquer à un fichier.

    Retourne par dossier (minuscules) la liste des YAMLs candidats avec leurs
    filename_keywords. Le watcher choisit le premier YAML dont les mots-clés
    correspondent au nom du fichier (ou le seul YAML du dossier si aucun keyword).
    """
    config_dir = Path("config/suppliers")
    mappings = load_all_mappings(config_dir)
    result: dict[str, list[dict]] = {}
    for rule in mappings.values():
        entry = {
            "supplier_code": rule.supplier_code,
            "filename_keywords": rule.filename_keywords,
        }
        for folder in rule.resolved_sharepoint_folders():
            result.setdefault(folder.lower(), []).append(entry)
    return result


@router.get(
    "/{supplier_code}/yaml",
    summary="Retourne le contenu YAML d'un fournisseur",
)
async def get_supplier_yaml(supplier_code: str) -> dict:
    """Retourne le contenu brut du YAML actif pour un fournisseur donné."""
    config_dir = Path("config/suppliers")
    yaml_file = config_dir / f"{supplier_code}_v1.yaml"
    if not yaml_file.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"YAML introuvable pour '{supplier_code}'")
    return {"supplier_code": supplier_code, "yaml_content": yaml_file.read_text(encoding="utf-8")}
