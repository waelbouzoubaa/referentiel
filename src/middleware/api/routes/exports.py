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


class ExportFileOut(BaseModel):
    filename: str
    size_bytes: int
    modified_at: str
    line_count: int  # lignes de données (en-tête exclu)


def _line_count(path: Path) -> int:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            n = sum(1 for _ in f)
        return max(n - 1, 0)
    except Exception:
        return 0


@router.get("/exports", response_model=list[ExportFileOut], tags=["exports"])
def list_exports() -> list[ExportFileOut]:
    """Liste les fichiers d'export Gery (CSV) générés, du plus récent au plus ancien."""
    if not EXPORTS_DIR.exists():
        return []
    result: list[ExportFileOut] = []
    for path in sorted(EXPORTS_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        result.append(
            ExportFileOut(
                filename=path.name,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                line_count=_line_count(path),
            )
        )
    return result


@router.get("/exports/{filename}/download", tags=["exports"])
def download_export(filename: str) -> FileResponse:
    """Télécharge un export CSV par son nom (protégé contre la traversée de chemin)."""
    if "/" in filename or "\\" in filename or not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    path = EXPORTS_DIR / filename
    if not path.is_file() or path.parent.resolve() != EXPORTS_DIR.resolve():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {filename}")
    return FileResponse(path, media_type="text/csv", filename=filename)
