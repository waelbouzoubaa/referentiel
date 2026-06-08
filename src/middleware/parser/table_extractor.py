from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from middleware.core.exceptions import ParsingError
from middleware.core.logging import get_logger
from middleware.parser.excel_reader import Row, Sheet, find_sheet, get_cell_value, read_workbook
from middleware.parser.grammar import (
    AttributeMapping,
    CellExtraction,
    ColumnMapping,
    FileMetadataMapping,
    MappingRule,
    PriceMapping,
    RowFilter,
    TableMappingConfig,
)
from middleware.parser.pivot import (
    AttributePivot,
    CommercialRulePivot,
    FileMetadataPivot,
    ParsingResult,
    PricePivot,
    ProductPivot,
)
from middleware.parser.transforms import (
    apply_transform,
    cell_ref_to_row_col,
    col_letter_to_idx,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def parse_table_file(path: Path, rule: MappingRule) -> ParsingResult:
    """Parse un fichier Excel en mode `table` selon la règle YAML fournie.

    Args:
        path: Chemin vers le fichier .xlsx.
        rule: Règle de mapping validée (extraction_mode=table).

    Returns:
        ParsingResult avec la liste des ProductPivot et les erreurs par ligne.

    Raises:
        ParsingError: Si le fichier est illisible ou la feuille introuvable.
    """
    assert rule.extraction_mode == "table", "parse_table_file requiert extraction_mode=table"

    sheets = read_workbook(path, sheet_name=_sheet_name_or_none(rule.sheet_match))
    sheet_name, sheet = find_sheet(sheets, rule.sheet_match)

    logger.info(
        "parsing mode table démarré",
        supplier_code=rule.supplier_code,
        fichier=path.name,
        feuille=sheet_name,
        lignes_total=len(sheet),
    )

    config = rule.as_table_config()
    file_metadata = _extract_file_metadata(sheet, rule.file_metadata)

    products: list[ProductPivot] = []
    error_count = 0

    data_start = rule.data_starts_row - 1  # 0-indexed
    data_rows = sheet[data_start:]

    for i, row in enumerate(data_rows):
        row_number = rule.data_starts_row + i

        if not _row_passes_filter(row, config.row_filter):
            continue

        try:
            product = _extract_product(row, row_number, rule.supplier_code, config)
            products.append(product)
        except Exception as exc:
            error_count += 1
            logger.warning(
                "erreur extraction ligne",
                supplier_code=rule.supplier_code,
                fichier=path.name,
                ligne=row_number,
                erreur=str(exc),
            )

    logger.info(
        "parsing terminé",
        supplier_code=rule.supplier_code,
        produits_extraits=len(products),
        erreurs=error_count,
    )

    return ParsingResult(
        supplier_code=rule.supplier_code,
        filename=path.name,
        products=products,
        file_metadata=file_metadata,
        error_count=error_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extraction d'un produit depuis une ligne
# ─────────────────────────────────────────────────────────────────────────────

def _extract_product(
    row: Row,
    row_number: int,
    supplier_code: str,
    config: TableMappingConfig,
) -> ProductPivot:
    """Extrait un ProductPivot depuis une ligne de données."""

    fields: dict[str, Any] = {}

    for field_name, col_mapping in config.columns.items():
        value = _extract_column_value(row, col_mapping)
        if value is None and col_mapping.required:
            raise ParsingError(
                f"Champ obligatoire '{field_name}' vide à la ligne {row_number}.",
                row_number=row_number,
            )
        if value is not None:
            fields[field_name] = value

    prices = [_extract_price(row, pm) for pm in config.prices]
    prices = [p for p in prices if p is not None]

    attributes = [_extract_attribute(row, am) for am in config.attributes]
    attributes = [a for a in attributes if a is not None]

    return ProductPivot(
        supplier_code=supplier_code,
        supplier_product_code=str(fields.get("supplier_product_code", "")),
        designation=str(fields.get("designation", "")),
        product_kind=fields.get("product_kind", "physical"),
        family=fields.get("family"),
        subfamily=fields.get("subfamily"),
        generic_code=fields.get("generic_code"),
        prices=prices,
        attributes=attributes,
        source_row=row_number,
    )


def _extract_column_value(row: Row, mapping: ColumnMapping) -> Any:
    """Extrait et transforme la valeur selon la définition ColumnMapping."""
    if mapping.constant is not None:
        return mapping.constant

    if mapping.source_col is not None:
        idx = col_letter_to_idx(mapping.source_col)
        raw = row[idx] if idx < len(row) else None
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        return apply_transform(raw, mapping.transform)

    if mapping.derived_from is not None:
        # La valeur dérivée est calculée après coup (cf. champ designation pour Airisol)
        return None

    return None


def _extract_price(row: Row, mapping: PriceMapping) -> PricePivot | None:
    """Extrait un prix depuis la ligne selon la définition PriceMapping."""
    idx = col_letter_to_idx(mapping.source_col)
    raw = row[idx] if idx < len(row) else None
    if raw is None:
        return None
    try:
        amount = apply_transform(raw, mapping.transform)
        if amount is None:
            return None
        return PricePivot(
            price_type=mapping.type,
            amount=Decimal(str(amount)),
            currency=mapping.currency,
        )
    except (ValueError, Exception):
        return None


def _extract_attribute(row: Row, mapping: AttributeMapping) -> AttributePivot | None:
    """Extrait un attribut technique depuis la ligne."""
    idx = col_letter_to_idx(mapping.source_col)
    raw = row[idx] if idx < len(row) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = apply_transform(raw, mapping.transform)
        if value is None:
            return None
        return AttributePivot(
            key=mapping.key,
            value=str(value),
            data_type=mapping.data_type,
            unit=mapping.unit,
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Filtre de lignes
# ─────────────────────────────────────────────────────────────────────────────

def _row_passes_filter(row: Row, row_filter: RowFilter) -> bool:
    """Retourne True si la ligne ne doit pas être exclue."""
    for col in row_filter.must_have_value_in:
        idx = col_letter_to_idx(col)
        val = row[idx] if idx < len(row) else None
        if val is None or (isinstance(val, str) and not val.strip()):
            return False

    if row_filter.must_have_value_in_any:
        has_any = False
        for col in row_filter.must_have_value_in_any:
            idx = col_letter_to_idx(col)
            val = row[idx] if idx < len(row) else None
            if val is not None and not (isinstance(val, str) and not val.strip()):
                has_any = True
                break
        if not has_any:
            return False

    for prefix in row_filter.exclude_if_starts_with:
        first_val = next((v for v in row if v is not None), None)
        if first_val is not None and str(first_val).startswith(prefix):
            return False

    return True


# ─────────────────────────────────────────────────────────────────────────────
# Extraction des métadonnées du fichier (cartouche)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_file_metadata(sheet: Sheet, mapping: FileMetadataMapping) -> FileMetadataPivot:
    """Extrait les métadonnées du cartouche fournisseur (dates, références, etc.)."""
    import re

    meta: dict[str, Any] = {}

    def _get(extraction: CellExtraction | None) -> Any:
        if extraction is None:
            return None
        if extraction.constant:
            return extraction.constant
        if extraction.cell:
            row_i, col_i = cell_ref_to_row_col(extraction.cell)
            raw = get_cell_value(sheet, row_i, col_i)
            if raw is None:
                return None
            return apply_transform(raw, extraction.transform)
        if extraction.regex and extraction.in_cell:
            row_i, col_i = cell_ref_to_row_col(extraction.in_cell)
            raw = get_cell_value(sheet, row_i, col_i)
            if raw is None:
                return None
            m = re.search(extraction.regex, str(raw))
            if m:
                value = m.group(1) if m.lastindex else m.group(0)
                return apply_transform(value, extraction.transform)
        return None

    try:
        meta["validity_start"] = _get(mapping.validity_start)
    except Exception:
        pass
    try:
        meta["validity_end"] = _get(mapping.validity_end)
    except Exception:
        pass
    try:
        meta["contract_reference"] = _get(mapping.contract_reference)
    except Exception:
        pass
    try:
        meta["geographic_scope"] = _get(mapping.geographic_scope)
    except Exception:
        pass
    try:
        meta["organizational_scope"] = _get(mapping.organizational_scope)
    except Exception:
        pass

    return FileMetadataPivot(**{k: v for k, v in meta.items() if v is not None})


# ─────────────────────────────────────────────────────────────────────────────
# Calcul du business_hash
# ─────────────────────────────────────────────────────────────────────────────

def compute_business_hash(product: ProductPivot) -> str:
    """Calcule le SHA-256 du tuple métier canonique d'un produit.

    Ce hash permet de détecter si un produit a changé entre deux ingestions.
    Seuls les champs métier sont inclus — pas les IDs, timestamps ou sources.
    """
    canonical = json.dumps(
        {
            "designation": product.designation.strip().upper(),
            "family": (product.family or "").strip().upper(),
            "subfamily": (product.subfamily or "").strip().upper(),
            "prices": sorted(
                [
                    (p.price_type, str(p.amount), p.currency, str(p.tier_min_quantity), str(p.tier_max_quantity))
                    for p in product.all_prices()
                ]
            ),
            "attributes": sorted(
                [(a.key, a.value, a.unit or "") for a in product.attributes]
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_business_hash_no_prices(product: ProductPivot) -> str:
    """Calcule le SHA-256 du tuple métier canonique d'un produit, sans les prix.

    Permet de distinguer un PRICE_CHANGE (seuls les prix diffèrent) d'un UPDATE
    (un champ métier comme la désignation a changé) en comparant ce hash à celui
    stocké en base lors de la dernière ingestion.
    """
    canonical = json.dumps(
        {
            "designation": product.designation.strip().upper(),
            "family": (product.family or "").strip().upper(),
            "subfamily": (product.subfamily or "").strip().upper(),
            "attributes": sorted(
                [(a.key, a.value, a.unit or "") for a in product.attributes]
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def _sheet_name_or_none(sheet_match: str | dict[str, str]) -> str | None:
    """Retourne le nom de feuille si c'est un nom exact, sinon None."""
    if isinstance(sheet_match, str) and sheet_match not in ("auto",):
        return sheet_match
    return None
