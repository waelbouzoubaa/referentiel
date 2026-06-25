from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from middleware.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

EXPORTS_DIR = Path("/app/exports")
CONFIG_DIR = Path("config/suppliers")


class ExportFileOut(BaseModel):
    folder: str
    filename: str
    supplier_code: str
    size_bytes: int
    modified_at: str
    line_count: int


def _line_count(path: Path) -> int:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            n = sum(1 for _ in f)
        return max(n - 1, 0)
    except Exception:
        return 0


def _supplier_code_from_filename(filename: str) -> str:
    """Extrait le supplier_code depuis le nom de fichier NEW_ARTICLE_{code}_{ts}.csv."""
    name = filename.replace(".csv", "")
    parts = name.split("_")
    # Format : NEW_ARTICLE_supplier_code_20260625-120000 → retire NEW, ARTICLE et le timestamp
    if len(parts) >= 3:
        # retire le préfixe NEW_ARTICLE_ et le suffixe timestamp (dernier segment)
        return "_".join(parts[2:-1]) if len(parts) > 3 else parts[2]
    return name


@router.get("/exports", response_model=list[ExportFileOut], tags=["exports"])
def list_exports() -> list[ExportFileOut]:
    """Liste les exports Gery groupés par dossier fournisseur, du plus récent au plus ancien."""
    if not EXPORTS_DIR.exists():
        return []
    result: list[ExportFileOut] = []
    # Parcourt les sous-dossiers (un par fournisseur) + les CSV à la racine (anciens)
    csv_files = list(EXPORTS_DIR.rglob("*.csv"))
    for path in sorted(csv_files, key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        folder = path.parent.name if path.parent != EXPORTS_DIR else "racine"
        supplier_code = _supplier_code_from_filename(path.name)
        result.append(
            ExportFileOut(
                folder=folder,
                filename=path.name,
                supplier_code=supplier_code,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                line_count=_line_count(path),
            )
        )
    return result


@router.get("/exports/{folder}/{filename}/download", tags=["exports"])
def download_export(folder: str, filename: str) -> FileResponse:
    """Télécharge un export CSV depuis son dossier fournisseur."""
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    path = EXPORTS_DIR / folder / filename
    if not path.is_file():
        # Fallback : fichier à la racine (anciens exports)
        path = EXPORTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {folder}/{filename}")
    return FileResponse(path, media_type="text/csv", filename=filename)


@router.get("/exports/{folder}/{filename}/yaml", tags=["exports"])
def get_export_yaml(folder: str, filename: str) -> dict:
    """Retourne le YAML qui a été appliqué pour générer cet export."""
    supplier_code = _supplier_code_from_filename(filename)
    yaml_file = CONFIG_DIR / f"{supplier_code}_v1.yaml"
    if not yaml_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"YAML introuvable pour '{supplier_code}' (fichier : {yaml_file.name})",
        )
    return {"supplier_code": supplier_code, "yaml_content": yaml_file.read_text(encoding="utf-8")}


# Rétrocompatibilité : ancien format sans dossier
@router.get("/exports/{filename}/download", tags=["exports"])
def download_export_legacy(filename: str) -> FileResponse:
    return download_export(".", filename)
