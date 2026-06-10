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

    # Détection dynamique des blocs de paliers si activée
    tier_block_map: dict[int, list[ColumnGroup]] = {}
    if config.price_matrix.tier_axis.detect_per_block:
        tier_block_map = _build_tier_block_map(sheet, row_start, row_end, config)

    products: list[ProductPivot] = []
    error_count = 0

    for row_0 in range(row_start - 1, row_end):
        row_number = row_0 + 1
        if row_0 >= len(sheet):
            break
        row = sheet[row_0]

        if not _row_passes_filter(row, config.row_filter):
            continue

        active_groups = tier_block_map.get(row_number, config.price_matrix.column_groups) if tier_block_map else config.price_matrix.column_groups

        try:
            product = _extract_matrix_product(row, row_number, rule.supplier_code, config, active_groups)
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
# Détection dynamique des blocs de paliers
# ─────────────────────────────────────────────────────────────────────────────

def _build_tier_block_map(
    sheet: Sheet,
    row_start: int,
    row_end: int,
    config: MatrixMappingConfig,
) -> dict[int, list[ColumnGroup]]:
    """Pré-scanne la feuille pour détecter les changements de paliers inter-blocs.

    Retourne un mapping row_number → column_groups actifs pour cette ligne.
    Les lignes d'en-tête de palier (désignation vide + texte de palier dans les
    colonnes de prix) mettent à jour le contexte courant pour les lignes suivantes.
    """
    desig_mapping = config.product_columns.get("designation")
    if desig_mapping is None or desig_mapping.source_col is None:
        return {}

    desig_idx = col_letter_to_idx(desig_mapping.source_col)
    first_col_indices = [col_letter_to_idx(g.columns[0]) for g in config.price_matrix.column_groups]

    current_groups: list[ColumnGroup] = list(config.price_matrix.column_groups)
    row_to_groups: dict[int, list[ColumnGroup]] = {}

    for row_0 in range(row_start - 1, row_end):
        row_number = row_0 + 1
        if row_0 >= len(sheet):
            break
        row = sheet[row_0]

        desig_val = row[desig_idx] if desig_idx < len(row) else None
        desig_empty = desig_val is None or (isinstance(desig_val, str) and not str(desig_val).strip())

        if desig_empty:
            tier_labels = _try_read_tier_labels(row, first_col_indices)
            if tier_labels and len(tier_labels) == len(config.price_matrix.column_groups):
                current_groups = [
                    ColumnGroup(
                        columns=g.columns,
                        tier_label=tier_labels[i],
                        variants=g.variants,
                    )
                    for i, g in enumerate(config.price_matrix.column_groups)
                ]
        else:
            row_to_groups[row_number] = current_groups

    return row_to_groups


def _try_read_tier_labels(row: Row, col_indices: list[int]) -> list[str] | None:
    """Tente de lire des libellés de palier depuis les colonnes données.

    Retourne la liste des libellés si toutes les colonnes contiennent du texte
    ressemblant à un palier (ex. '0-500m²', '>1000m²'), None sinon.
    """
    labels = []
    for idx in col_indices:
        val = row[idx] if idx < len(row) else None
        if val is None or not isinstance(val, str):
            return None
        text = val.strip()
        if not text or not _looks_like_tier_label(text):
            return None
        labels.append(text)
    return labels or None


def _looks_like_tier_label(text: str) -> bool:
    """Vrai si le texte ressemble à un palier de quantité : '0-500m²' ou '>1000m²'."""
    text = text.strip()
    return bool(
        re.match(r"^>\s*\d", text) or
        re.match(r"^\d+\s*[-–]\s*\d+", text)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Extraction d'un produit
# ─────────────────────────────────────────────────────────────────────────────

def _extract_matrix_product(
    row: Row,
    row_number: int,
    supplier_code: str,
    config: MatrixMappingConfig,
    column_groups: list[ColumnGroup] | None = None,
) -> ProductPivot:
    """Extrait un ProductPivot avec ses variantes/prix depuis une ligne de la matrice."""
    if column_groups is None:
        column_groups = config.price_matrix.column_groups

    fields: dict[str, Any] = {}
    for field_name, col_mapping in config.product_columns.items():
        if col_mapping.derived_from and "{" in col_mapping.derived_from:
            continue  # résolu après les attributs
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

    # Résolution des champs dérivés (template avec {variable}) après attributs
    attr_dict = {a.key: _format_attr_value(a.value) for a in attributes}
    for field_name, col_mapping in config.product_columns.items():
        if col_mapping.derived_from and "{" in col_mapping.derived_from:
            template_vars = {k: str(v) for k, v in fields.items() if v is not None}
            template_vars.update(attr_dict)
            rendered = _render_derived_template(col_mapping.derived_from, template_vars)
            if not rendered and col_mapping.required:
                raise ParsingError(
                    f"Champ dérivé obligatoire '{field_name}' vide à la ligne {row_number}.",
                    row_number=row_number,
                )
            if rendered:
                fields[field_name] = rendered

    variants, direct_prices = _extract_variants(row, column_groups, config.price_matrix)

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
        prices=direct_prices,
        attributes=attributes,
        commercial_rules=commercial_rules,
        source_row=row_number,
    )


def _render_derived_template(template: str, template_vars: dict[str, str]) -> str:
    """Rend un champ dérivé `derived_from`, segment par segment (séparés par `|`).

    Un segment dont une variable `{var}` est absente/vide est omis (cf. `_render_code`
    dans gery.py). Permet à un même template de couvrir des lignes hétérogènes,
    ex. "{designation} | EP{epaisseur}" → "ISOLLIN EP50" si epaisseur connu,
    "ISOVAP" si epaisseur absent.
    """
    segments = []
    for segment in template.split("|"):
        segment = segment.strip()
        var_names = re.findall(r"\{(\w+)\}", segment)
        if var_names and any(not template_vars.get(v) for v in var_names):
            continue
        rendered = re.sub(r"\{(\w+)\}", lambda m: template_vars.get(m.group(1), ""), segment)
        if rendered:
            segments.append(rendered)
    return " ".join(segments)


def _format_attr_value(value: str) -> str:
    """Formate une valeur d'attribut : '50.0' → '50', '1.25' reste '1.25'."""
    try:
        f = float(value)
        if f == int(f):
            return str(int(f))
    except (ValueError, TypeError):
        pass
    return value


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
) -> tuple[list[VariantPivot], list[PricePivot]]:
    """Extrait les variantes et prix d'une ligne de la matrice.

    Retourne (variants, direct_prices) :
    - Si le produit a plusieurs variantes avec des données : (variants, [])
    - Si une seule colonne de variante est remplie sur toutes les colonnes secondaires :
      c'est un produit sans variante → ([], prices)
    """
    dimension = price_matrix_config.variant_axis.dimension_name
    price_type = price_matrix_config.price_type
    currency = price_matrix_config.currency
    transform = price_matrix_config.transform

    variant_prices: dict[str, list[PricePivot]] = {}
    has_secondary_data = False

    for group in column_groups:
        tier_min, tier_max, tier_unit = _parse_tier_label(group.tier_label)

        for col_pos, (col_letter, variant_name) in enumerate(zip(group.columns, group.variants)):
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
                    tier_label=group.tier_label,
                    tier_min_quantity=tier_min,
                    tier_max_quantity=tier_max,
                    tier_unit=tier_unit,
                )
                if variant_name not in variant_prices:
                    variant_prices[variant_name] = []
                variant_prices[variant_name].append(price)
                if col_pos > 0:
                    has_secondary_data = True
            except Exception:
                continue

    active_variants = [k for k, v in variant_prices.items() if v]

    # Produit sans variante réelle : une seule variante nommée, aucune colonne secondaire remplie
    multi_variant_defined = any(len(g.variants) > 1 for g in column_groups)
    if len(active_variants) == 1 and not has_secondary_data and multi_variant_defined:
        return [], variant_prices[active_variants[0]]

    # Produit matriciel : une VariantPivot par variante
    variants: list[VariantPivot] = []
    for order, (variant_name, prices) in enumerate(variant_prices.items()):
        if prices:
            variants.append(VariantPivot(
                variant_dimension=dimension,
                variant_value=variant_name,
                variant_code=variant_name.upper(),
                display_order=order,
                prices=prices,
            ))

    return variants, []


def _parse_tier_label(label: str) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Parse un tier_label comme '0-500m²', '500-1000m²', '>1000m²'.

    Returns (tier_min, tier_max, unit).
    """
    label = label.strip()

    m = re.match(r">(\d+(?:[.,]\d+)?)\s*(\S*)", label)
    if m:
        unit = m.group(2) or None
        return Decimal(m.group(1).replace(",", ".")), None, unit

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
