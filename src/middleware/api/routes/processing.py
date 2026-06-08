from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.api.schemas import (
    DeltaSummary,
    GenerateExportsRequest,
    GenerateExportsResponse,
    GeneratedFileOut,
    ProcessFileRequest,
    ProcessFileResponse,
)
from middleware.core.logging import get_logger
from middleware.db.session import get_session
from middleware.db.writer import (
    get_known_hashes,
    get_or_create_supplier,
    get_or_create_supplier_file,
    mark_file_processed,
    persist_delta,
    persist_gery_export,
)
from middleware.delta.engine import compute_delta
from middleware.exporter.gery import generate_gery_exports
from middleware.parser.grammar import MappingRule
from middleware.parser.matrix_extractor import parse_matrix_file
from middleware.parser.multi_table_extractor import parse_multi_table_file
from middleware.parser.table_extractor import parse_table_file
from middleware.parser.yaml_loader import load_all_mappings

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
    """Parse un fichier fournisseur, calcule le delta réel vs PostgreSQL, persiste en base et génère le fichier NEW_ARTICLE Gery."""
    path = Path(request.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {request.file_path}")

    rule = _load_rule(request.supplier_code)
    result = _parse_with_rule(path, rule)

    # Supplier + fichier (idempotent via content_hash)
    supplier = await get_or_create_supplier(session, request.supplier_code)
    supplier_file = await get_or_create_supplier_file(session, supplier, path)

    # Delta réel depuis l'état PostgreSQL
    incoming_codes = {p.supplier_product_code for p in result.products}
    known_hashes, known_hashes_no_prices, deleted_codes = await get_known_hashes(
        session, supplier.id, rule.upload_mode, incoming_codes
    )
    delta = compute_delta(
        result.products,
        known_hashes=known_hashes,
        known_hashes_no_prices=known_hashes_no_prices,
        deleted_codes=deleted_codes,
    )

    # Persistance delta
    await persist_delta(session, delta, supplier.id, supplier_file.id)

    # Export Gery (seulement NEW_ARTICLE)
    output_dir = Path(request.output_dir)
    export_result = generate_gery_exports(
        delta=delta,
        export_config=rule.gery_export,
        supplier_code=request.supplier_code,
        output_dir=output_dir,
        validity_start=result.file_metadata.validity_start,
        validity_end=result.file_metadata.validity_end,
    )

    # Persistance export Gery en base + marque product_history
    await persist_gery_export(session, export_result, supplier.id, supplier_file.id)
    await mark_file_processed(session, supplier_file)

    logger.info(
        "generate-gery-exports terminé",
        supplier_code=request.supplier_code,
        fichier=path.name,
        produits=len(result.products),
        creates=len(delta.creates),
        updates=len(delta.updates),
        price_changes=len(delta.price_changes),
        deletes=len(delta.deletes),
        reactivates=len(delta.reactivates),
        unchanged=delta.unchanged,
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
    """Dispatch vers le bon parseur selon extraction_mode."""
    if rule.extraction_mode == "table":
        return parse_table_file(path, rule)
    elif rule.extraction_mode == "matrix":
        return parse_matrix_file(path, rule)
    elif rule.extraction_mode == "multi_table":
        return parse_multi_table_file(path, rule)
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Mode d'extraction non supporté : '{rule.extraction_mode}'",
        )
