"""Service de traitement partagé : parse → delta → persistance DB → export Gery.

Utilisé à la fois par l'endpoint `/generate-gery-exports` (fournisseur connu) et
par la validation Streamlit `approve` (nouveau fournisseur), pour garantir le même
comportement : archivage MinIO du fichier brut, écriture en base et génération du
fichier Gery, de façon cohérente sur les deux chemins.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from middleware.core.logging import get_logger
from middleware.db.writer import (
    get_known_hashes,
    get_or_create_supplier,
    get_or_create_supplier_file,
    mark_file_processed,
    persist_delta,
    persist_gery_export,
)
from middleware.delta.engine import DeltaResult, compute_delta
from middleware.exporter.gery import GeryExportResult, generate_gery_exports
from middleware.parser.grammar import MappingRule
from middleware.parser.matrix_extractor import parse_matrix_file
from middleware.parser.multi_table_extractor import parse_multi_table_file
from middleware.parser.pivot import ParsingResult
from middleware.parser.table_extractor import parse_table_file
from middleware.sage_codes import resolve_sage_code

logger = get_logger(__name__)


def parse_with_rule(path: Path, rule: MappingRule) -> ParsingResult:
    """Dispatch vers le bon parseur selon `extraction_mode`."""
    if rule.extraction_mode == "table":
        return parse_table_file(path, rule)
    if rule.extraction_mode == "matrix":
        return parse_matrix_file(path, rule)
    if rule.extraction_mode == "multi_table":
        return parse_multi_table_file(path, rule)
    raise ValueError(f"Mode d'extraction non supporté : '{rule.extraction_mode}'")


async def process_and_export(
    session: AsyncSession,
    rule: MappingRule,
    file_path: Path,
    output_dir: Path,
    *,
    original_filename: str | None = None,
    sharepoint_item_id: str | None = None,
) -> tuple[ParsingResult, DeltaResult, GeryExportResult]:
    """Traite un fichier fournisseur de bout en bout.

    Parse le fichier, archive le brut en MinIO (via `get_or_create_supplier_file`),
    calcule le delta vs l'état PostgreSQL, persiste les changements, génère le fichier
    Gery puis trace l'export en base.

    Returns:
        (résultat de parsing, delta calculé, résultat d'export).
    """
    result = parse_with_rule(file_path, rule)

    supplier = await get_or_create_supplier(session, rule.supplier_code)
    supplier_file = await get_or_create_supplier_file(
        session,
        supplier,
        file_path,
        original_filename=original_filename,
        sharepoint_item_id=sharepoint_item_id,
    )

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

    await persist_delta(session, delta, supplier.id, supplier_file.id)

    export_result = generate_gery_exports(
        delta=delta,
        export_config=rule.gery_export,
        supplier_code=rule.supplier_code,
        output_dir=output_dir,
        validity_start=result.file_metadata.validity_start,
        validity_end=result.file_metadata.validity_end,
        code_fournisseur_sage=resolve_sage_code(rule.supplier_code),
    )

    await persist_gery_export(session, export_result, supplier.id, supplier_file.id)
    await mark_file_processed(session, supplier_file)

    logger.info(
        "pipeline traité",
        supplier_code=rule.supplier_code,
        fichier=file_path.name,
        produits=len(result.products),
        creates=len(delta.creates),
        updates=len(delta.updates),
        price_changes=len(delta.price_changes),
        deletes=len(delta.deletes),
        reactivates=len(delta.reactivates),
        unchanged=delta.unchanged,
    )
    return result, delta, export_result
