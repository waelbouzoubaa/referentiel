from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from middleware.core.exceptions import ParsingError
from middleware.core.logging import get_logger
from middleware.parser.excel_reader import Row, Sheet, find_sheet, get_cell_value, read_workbook
from middleware.parser.grammar import (
    AttributeMapping,
    ColumnGroup,
    ColumnMapping,
    CommercialRuleMapping,
    MatrixMappingConfig,
    MappingRule,
    RowFilter,
)
from middleware.parser.pivot import (
    AttributePivot,
    CommercialRulePivot,
    FileMetadataPivot,
    ParsingResult,
    PricePivot,
    ProductPivot,
    VariantPivot,
)
from middleware.parser.transforms import apply_transform, cell_ref_to_row_col, col_letter_to_idx
from middleware.parser.table_extractor import _extract_file_metadata, _row_passes_filter

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def parse_matrix_file(path: Path, rule: MappingRule) -> ParsingResult:
    """Parse un fichier Excel en mode `matrix` selon la règle YAML fournie.

    Utilisé pour Airisol : matrice de prix paliers × variantes (ALU/BLANC).

    Args:
        path: Chemin vers le fichier .xlsx.
        rule: Règle de mapping validée (extraction_mode=matrix).

    Returns:
        ParsingResult avec la liste des ProductPivot et les erreurs par ligne.

    Raises:
        ParsingError: Si le fichier est illisible ou la feuille introuvable.
    """
    assert rule.extraction_mode == "matrix", "parse_matrix_file requiert extraction_mode=matrix"

    sheets = read_workbook(path, sheet_name=_sheet_name_or_none(rule.sheet_match))
    sheet_name, sheet = find_sheet(sheets, rule.sheet_match)

    logger.info(
        "parsing mode matrix démarré",
        supplier_code=rule.supplier_code,
        fichier=path.name,
        feuille=sheet_name,
    )

    config = rule.as_matrix_config()
    file_metadata = _extract_file_metadata(sheet, rule.file_metadata)

    row_start, row_end = _parse_row_range(config.data_zone.rows)

    products: list[ProductPivot] = []
    error_count = 0

    for row_0 in range(row_start - 1, row_end):
        row_number = row_0 + 1
        if row_0 >= len(sheet):
            break
        row = sheet[row_0]

        if not _row_passes_filter(row, config.row_filter):
            continue

        try:
            product = _extract_matrix_product(row, row_number, rule.supplier_code, config)
            products.append(product)
        except Exception as exc:
            error_count += 1
            logger.warning(
                "erreur extraction ligne matrix",
                supplier_code=rule.supplier_code,
                fichier=path.name,
                ligne=row_number,
                erreur=str(exc),
            )

    logger.info(
        "parsing matrix terminé",
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
# Extraction d'un produit
# ─────────────────────────────────────────────────────────────────────────────

def _extract_matrix_product(
    row: Row,
    row_number: int,
    supplier_code: str,
    config: MatrixMappingConfig,
) -> ProductPivot:
    """Extrait un ProductPivot avec ses variantes/prix depuis une ligne de la matrice."""
    fields: dict[str, Any] = {}
    for field_name, col_mapping in config.product_columns.items():
        value = _extract_col_value(row, col_mapping)
        if value is None and col_mapping.required:
            raise ParsingError(
                f"Champ obligatoire '{field_name}' vide à la ligne {row_number}.",
                row_number=row_number,
            )
        if value is not None:
            fields[field_name] = value

    attributes = [_extract_attribute(row, am) for am in config.attributes]
    attributes = [a for a in attributes if a is not None]

    variants = _extract_variants(row, config.price_matrix.column_groups, config.price_matrix)

    commercial_rules = [_extract_commercial_rule(row, crm) for crm in config.commercial_rules]
    commercial_rules = [cr for cr in commercial_rules if cr is not None]

    return ProductPivot(
        supplier_code=supplier_code,
        supplier_product_code=str(fields.get("supplier_product_code", "")),
        designation=str(fields.get("designation", "")),
        product_kind="physical",
        family=fields.get("family"),
        subfamily=fields.get("subfamily"),
        variants=variants,
        attributes=attributes,
        commercial_rules=commercial_rules,
        source_row=row_number,
    )


def _extract_col_value(row: Row, mapping: ColumnMapping) -> Any:
    if mapping.constant is not None:
        return mapping.constant
    if mapping.source_col is not None:
        idx = col_letter_to_idx(mapping.source_col)
        raw = row[idx] if idx < len(row) else None
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        return apply_transform(raw, mapping.transform)
    return None


def _extract_attribute(row: Row, mapping: AttributeMapping) -> AttributePivot | None:
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
# Extraction des variantes et prix de la matrice
# ─────────────────────────────────────────────────────────────────────────────

def _extract_variants(
    row: Row,
    column_groups: list[ColumnGroup],
    price_matrix_config: Any,
) -> list[VariantPivot]:
    """Construit un VariantPivot par valeur de variante unique avec ses prix par palier."""
    dimension = price_matrix_config.variant_axis.dimension_name
    price_type = price_matrix_config.price_type
    currency = price_matrix_config.currency
    transform = price_matrix_config.transform

    # variant_name → list of PricePivot (one per tier)
    variant_prices: dict[str, list[PricePivot]] = {}

    for group_idx, group in enumerate(column_groups):
        tier_min, tier_max, tier_unit = _parse_tier_label(group.tier_label)

        for col_letter, variant_name in zip(group.columns, group.variants):
            idx = col_letter_to_idx(col_letter)
            raw = row[idx] if idx < len(row) else None
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                continue
            try:
                amount = apply_transform(raw, transform)
                if amount is None:
                    continue
                price = PricePivot(
                    price_type=price_type,
                    amount=Decimal(str(amount)),
                    currency=currency,
                    tier_min_quantity=tier_min,
                    tier_max_quantity=tier_max,
                    tier_unit=tier_unit,
                )
                if variant_name not in variant_prices:
                    variant_prices[variant_name] = []
                variant_prices[variant_name].append(price)
            except Exception:
                continue

    variants: list[VariantPivot] = []
    for order, (variant_name, prices) in enumerate(variant_prices.items()):
        variants.append(VariantPivot(
            variant_dimension=dimension,
            variant_value=variant_name,
            variant_code=variant_name.upper(),
            display_order=order,
            prices=prices,
        ))

    return variants


def _parse_tier_label(label: str) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Parse un tier_label comme '0-500m²', '500-1000m²', '>1000m²'.

    Returns (tier_min, tier_max, unit).
    """
    label = label.strip()

    # ">1000m²" ou ">1000"
    m = re.match(r">(\d+(?:[.,]\d+)?)\s*(\S*)", label)
    if m:
        unit = m.group(2) or None
        return Decimal(m.group(1).replace(",", ".")), None, unit

    # "0-500m²" ou "500-1000 m²"
    m = re.match(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)\s*(\S*)", label)
    if m:
        unit = m.group(3) or None
        return (
            Decimal(m.group(1).replace(",", ".")),
            Decimal(m.group(2).replace(",", ".")),
            unit,
        )

    return None, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Extraction des règles commerciales
# ─────────────────────────────────────────────────────────────────────────────

def _extract_commercial_rule(row: Row, mapping: CommercialRuleMapping) -> CommercialRulePivot | None:
    idx = col_letter_to_idx(mapping.source_col)
    raw = row[idx] if idx < len(row) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    raw_text = str(raw).strip()
    threshold_value: Decimal | None = None
    if mapping.parse_pattern:
        m = re.search(mapping.parse_pattern, raw_text)
        if m:
            try:
                threshold_value = Decimal(m.group(1).replace(",", "."))
            except Exception:
                pass
    return CommercialRulePivot(
        rule_type=mapping.rule_type,
        threshold_value=threshold_value,
        threshold_unit=mapping.threshold_unit,
        raw_text=raw_text,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def _parse_row_range(rows_spec: str) -> tuple[int, int]:
    """Parse '10:31' → (10, 31) (1-based, inclusive)."""
    parts = rows_spec.split(":")
    if len(parts) != 2:
        raise ParsingError(f"Format data_zone.rows invalide : '{rows_spec}'")
    return int(parts[0]), int(parts[1])


def _sheet_name_or_none(sheet_match: str | dict[str, str]) -> str | None:
    if isinstance(sheet_match, str) and sheet_match not in ("auto",):
        return sheet_match
    return None
