from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.api.schemas import (
    DeltaSummary,
    GeneratedFileOut,
    GenerateExportsRequest,
    GenerateExportsResponse,
    ProcessFileRequest,
    ProcessFileResponse,
)
from middleware.core.logging import get_logger
from middleware.db.session import get_session
from middleware.delta.engine import compute_delta
from middleware.parser.grammar import MappingRule
from middleware.parser.yaml_loader import load_all_mappings
from middleware.pipeline import parse_with_rule, process_and_export

logger = get_logger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# POST /process-file
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/process-file", response_model=ProcessFileResponse, tags=["traitement"])
async def process_file(request: ProcessFileRequest) -> ProcessFileResponse:
    """Parse un fichier fournisseur et calcule le delta par rapport à l'état connu.

    En mode dry_run=True, aucune donnée n'est persistée en base.
    La règle YAML est chargée depuis config/suppliers/.

    Args:
        request: supplier_code, file_path (local), dry_run flag.

    Returns:
        Résumé du parsing et du delta.
    """
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    rule = _load_rule(request.supplier_code)

    result = _parse_with_rule(path, rule)

    # Delta : aucun état connu en mode dry_run (simule une première ingestion)
    # En production, known_hashes viendrait de la base de données
    delta = compute_delta(result.products, known_hashes={})
    delta_summary = DeltaSummary(
        creates=len(delta.creates),
        updates=len(delta.updates),
        price_changes=len(delta.price_changes),
        deletes=len(delta.deletes),
        reactivates=len(delta.reactivates),
        unchanged=delta.unchanged,
        total_changes=delta.total_changes,
    )

    logger.info(
        "process-file terminé",
        supplier_code=request.supplier_code,
        fichier=path.name,
        produits=len(result.products),
        delta=delta_summary.model_dump(),
    )

    return ProcessFileResponse(
        supplier_code=result.supplier_code,
        filename=result.filename,
        products_parsed=len(result.products),
        error_count=result.error_count,
        delta=delta_summary,
        dry_run=request.dry_run,
        parsed_at=result.parsed_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /generate-gery-exports
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/generate-gery-exports", response_model=GenerateExportsResponse, tags=["exports"])
async def generate_gery_exports_endpoint(
    request: GenerateExportsRequest,
    session: AsyncSession = Depends(get_session),
) -> GenerateExportsResponse:
    """Traite un fichier (parse + delta vs PostgreSQL), persiste et génère le CSV Gery."""
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    rule = _load_rule(request.supplier_code)

    # Parse + archivage MinIO + delta vs PostgreSQL + persistance + export (service partagé)
    _, _, export_result = await process_and_export(
        session,
        rule,
        path,
        Path(request.output_dir),
        original_filename=request.original_filename,
        sharepoint_item_id=request.sharepoint_item_id,
    )

    return GenerateExportsResponse(
        supplier_code=request.supplier_code,
        files=[
            GeneratedFileOut(
                kind=f.kind,
                path=str(f.path),
                line_count=f.line_count,
                output_hash=f.output_hash,
            )
            for f in export_result.files
        ],
        generated_at=export_result.generated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_rule(supplier_code: str) -> MappingRule:
    """Charge la règle YAML active pour un fournisseur."""
    config_dir = Path("config/suppliers")
    mappings = load_all_mappings(config_dir)
    rule = mappings.get(supplier_code)
    if rule is None:
        raise HTTPException(
            status_code=404,
            detail=f"Fournisseur inconnu ou aucune règle YAML : '{supplier_code}'. "
                   f"Fournisseurs disponibles : {list(mappings.keys())}",
        )
    return rule


def _parse_with_rule(path: Path, rule: MappingRule):
    """Dispatch vers le bon parseur (cf. pipeline.parse_with_rule), erreurs en 422."""
    try:
        return parse_with_rule(path, rule)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
