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
    CellExtraction,
    ColDimension,
    FileMetadataMapping,
    MappingRule,
    MultiTableMappingConfig,
    PriceMapping,
    ProductTemplate,
    SubTable,
)
from middleware.parser.pivot import (
    AttributePivot,
    FileMetadataPivot,
    ParsingResult,
    PricePivot,
    ProductPivot,
)
from middleware.parser.transforms import apply_transform, cell_ref_to_row_col, col_letter_to_idx

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def parse_multi_table_file(path: Path, rule: MappingRule) -> ParsingResult:
    """Parse un fichier Excel en mode `multi_table` selon la règle YAML.

    Utilisé pour Agenor : plusieurs tableaux dans la même feuille avec des
    layouts hétérogènes (matrix_2D, barème_1D).

    Args:
        path: Chemin vers le fichier .xlsx.
        rule: Règle de mapping validée (extraction_mode=multi_table).

    Returns:
        ParsingResult avec la liste des ProductPivot et les erreurs.

    Raises:
        ParsingError: Si le fichier est illisible ou la feuille introuvable.
    """
    assert rule.extraction_mode == "multi_table"

    sheets = read_workbook(path, sheet_name=_sheet_name_or_none(rule.sheet_match))
    sheet_name, sheet = find_sheet(sheets, rule.sheet_match)

    logger.info(
        "parsing mode multi_table démarré",
        supplier_code=rule.supplier_code,
        fichier=path.name,
        feuille=sheet_name,
    )

    config = rule.as_multi_table_config()
    file_metadata = _extract_file_metadata_multi(sheet, rule.file_metadata)

    products: list[ProductPivot] = []
    error_count = 0

    for sub_table in config.tables:
        try:
            sub_products, sub_errors = _extract_sub_table(
                sheet, sub_table, rule.supplier_code, rule.product_kind
            )
            products.extend(sub_products)
            error_count += sub_errors
        except Exception as exc:
            logger.warning(
                "erreur extraction sous-tableau",
                supplier_code=rule.supplier_code,
                tableau=sub_table.name,
                erreur=str(exc),
            )
            error_count += 1

    logger.info(
        "parsing multi_table terminé",
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
# Dispatch selon le layout
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sub_table(
    sheet: Sheet,
    sub_table: SubTable,
    supplier_code: str,
    product_kind: str,
) -> tuple[list[ProductPivot], int]:
    row_start, row_end = _parse_row_range(sub_table.zone.data_rows)
    layout = sub_table.layout

    if layout == "matrix_2D":
        return _extract_matrix_2d(sheet, sub_table, supplier_code, product_kind, row_start, row_end)
    elif layout in ("barème_1D", "bareme_1D"):
        return _extract_bareme_1d(sheet, sub_table, supplier_code, product_kind, row_start, row_end)
    else:
        raise ParsingError(f"Layout non supporté : '{layout}'")


# ─────────────────────────────────────────────────────────────────────────────
# Layout matrix_2D
# ─────────────────────────────────────────────────────────────────────────────

def _extract_matrix_2d(
    sheet: Sheet,
    sub_table: SubTable,
    supplier_code: str,
    product_kind: str,
    row_start: int,
    row_end: int,
) -> tuple[list[ProductPivot], int]:
    """Génère nb_lignes × nb_col_dimensions ProductPivot."""
    products: list[ProductPivot] = []
    error_count = 0
    first_col = _first_col_of_zone(sub_table.zone.cols)

    for row_0 in range(row_start - 1, row_end):
        if row_0 >= len(sheet):
            break
        row = sheet[row_0]
        row_number = row_0 + 1

        row_value = _get_cell(row, first_col)
        if not row_value:
            continue

        for col_dim in sub_table.col_dimensions:
            try:
                product = _build_product_matrix_2d(
                    row, row_number, row_value, col_dim,
                    sub_table, supplier_code, product_kind,
                )
                products.append(product)
            except Exception as exc:
                error_count += 1
                logger.warning(
                    "erreur matrix_2D",
                    tableau=sub_table.name,
                    ligne=row_number,
                    col_dim=col_dim.value,
                    erreur=str(exc),
                )

    return products, error_count


def _build_product_matrix_2d(
    row: Row,
    row_number: int,
    row_value: str,
    col_dim: ColDimension,
    sub_table: SubTable,
    supplier_code: str,
    product_kind: str,
) -> ProductPivot:
    template = sub_table.product_template
    context = _build_template_context(row_value, col_dim)

    designation = _render_template(template.designation_template, context)
    code = _render_template(template.supplier_product_code_template, context)

    prices = [_extract_price_from_col(row, pm) for pm in sub_table.prices]
    prices = [p for p in prices if p is not None]

    # Override price source with col_dim.price_col
    if col_dim.price_col and sub_table.prices:
        prices = [_extract_price_single(row, col_dim.price_col, sub_table.prices[0])]
        prices = [p for p in prices if p is not None]

    attributes = []
    for am in sub_table.attributes:
        attr = _extract_attribute(row, am)
        if attr is not None:
            attributes.append(attr)
    # Also extract max_time from col_dim.max_time_col if present
    if col_dim.max_time_col and sub_table.attributes:
        # Find duration attribute and override with correct column
        for am in sub_table.attributes:
            if am.data_type == "duration" and am.source_col:
                corrected_am = am.model_copy(update={"source_col": col_dim.max_time_col})
                attr = _extract_attribute(row, corrected_am)
                if attr is not None:
                    # Replace if already added with wrong col
                    attributes = [a for a in attributes if a.key != am.key]
                    attributes.append(attr)

    return ProductPivot(
        supplier_code=supplier_code,
        supplier_product_code=code,
        designation=designation,
        product_kind=product_kind,
        family=template.family,
        subfamily=template.subfamily,
        prices=prices,
        attributes=attributes,
        source_row=row_number,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Layout barème_1D
# ─────────────────────────────────────────────────────────────────────────────

def _extract_bareme_1d(
    sheet: Sheet,
    sub_table: SubTable,
    supplier_code: str,
    product_kind: str,
    row_start: int,
    row_end: int,
) -> tuple[list[ProductPivot], int]:
    """Une ligne → un ProductPivot."""
    products: list[ProductPivot] = []
    error_count = 0
    first_col = _first_col_of_zone(sub_table.zone.cols)

    for row_0 in range(row_start - 1, row_end):
        if row_0 >= len(sheet):
            break
        row = sheet[row_0]
        row_number = row_0 + 1

        row_value = _get_cell(row, first_col)
        if not row_value:
            continue

        try:
            template = sub_table.product_template
            context = _build_template_context(row_value, col_dim=None)

            designation = _render_template(template.designation_template, context)
            code = _render_template(template.supplier_product_code_template, context)

            prices = [_extract_price_from_col(row, pm) for pm in sub_table.prices]
            prices = [p for p in prices if p is not None]

            attributes = [_extract_attribute(row, am) for am in sub_table.attributes]
            attributes = [a for a in attributes if a is not None]

            products.append(ProductPivot(
                supplier_code=supplier_code,
                supplier_product_code=code,
                designation=designation,
                product_kind=product_kind,
                family=template.family,
                subfamily=template.subfamily,
                prices=prices,
                attributes=attributes,
                source_row=row_number,
            ))
        except Exception as exc:
            error_count += 1
            logger.warning(
                "erreur barème_1D",
                tableau=sub_table.name,
                ligne=row_number,
                erreur=str(exc),
            )

    return products, error_count


# ─────────────────────────────────────────────────────────────────────────────
# Template rendering
# ─────────────────────────────────────────────────────────────────────────────

def _build_template_context(row_value: str, col_dim: ColDimension | None) -> dict[str, str]:
    """Construit le contexte de substitution pour les templates."""
    context: dict[str, str] = {}

    # All {var} in templates that don't match col_dim.key → row_value
    # We don't know the var names ahead of time, so we store under special keys
    # and the renderer resolves them.
    context["__row_value__"] = row_value
    context["__row_value_slug__"] = _slugify(row_value)

    if col_dim is not None:
        context[col_dim.key] = col_dim.value
        context[col_dim.key + "_slug"] = _slugify(col_dim.value)

    return context


def _render_template(template: str, context: dict[str, str]) -> str:
    """Rend un template de désignation ou de code avec substitution des variables."""
    col_dim_keys = {k for k in context if not k.startswith("__")}
    row_value = context["__row_value__"]
    row_value_slug = context["__row_value_slug__"]

    vars_in_template = re.findall(r"\{(\w+)\}", template)

    render_ctx: dict[str, str] = {}
    for var in vars_in_template:
        if var in col_dim_keys:
            render_ctx[var] = context[var]
        elif var in context:
            render_ctx[var] = context[var]
        elif var.endswith("_slug"):
            # Check if the base key is a col_dim key
            base = var[:-5]  # remove "_slug"
            if base + "_slug" in col_dim_keys:
                render_ctx[var] = context.get(base + "_slug", _slugify(context.get(base, "")))
            else:
                render_ctx[var] = row_value_slug
        else:
            render_ctx[var] = row_value

    return template.format(**render_ctx)


def _slugify(value: str) -> str:
    """Convertit une chaîne en slug ASCII (pour les codes produits)."""
    slug = value.strip().upper()
    slug = re.sub(r"[^A-Z0-9]+", "_", slug)
    return slug.strip("_")


# ─────────────────────────────────────────────────────────────────────────────
# Extraction prix et attributs
# ─────────────────────────────────────────────────────────────────────────────

def _extract_price_from_col(row: Row, mapping: PriceMapping) -> PricePivot | None:
    return _extract_price_single(row, mapping.source_col, mapping)


def _extract_price_single(row: Row, col: str, mapping: PriceMapping) -> PricePivot | None:
    idx = col_letter_to_idx(col)
    raw = row[idx] if idx < len(row) else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
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
    except Exception:
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
# Extraction des métadonnées fichier (gère captures multi-groupes)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_file_metadata_multi(sheet: Sheet, mapping: FileMetadataMapping) -> FileMetadataPivot:
    """Extrait les métadonnées, avec support des regex multi-captures (Agenor)."""
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
            if m and not extraction.captures:
                value = m.group(1) if m.lastindex else m.group(0)
                return apply_transform(value, extraction.transform)
        return None

    # Champs standards
    _str_fields = {"contract_reference", "geographic_scope", "organizational_scope",
                   "client_article_code", "siren_fournisseur"}
    for field in ("validity_start", "validity_end", "contract_reference",
                  "geographic_scope", "organizational_scope", "client_article_code",
                  "siren_fournisseur"):
        extraction = getattr(mapping, field, None)
        if extraction is not None:
            try:
                value = _get(extraction)
                if field in _str_fields and value is not None:
                    value = str(value)
                meta[field] = value
            except Exception:
                pass

    # validity_period avec captures multi-groupes (Agenor)
    if mapping.validity_period and mapping.validity_period.captures:
        extraction = mapping.validity_period
        try:
            row_i, col_i = cell_ref_to_row_col(extraction.in_cell)
            raw = get_cell_value(sheet, row_i, col_i)
            if raw is not None:
                m = re.search(extraction.regex, str(raw))
                if m:
                    for field_name, group_idx in extraction.captures.items():
                        try:
                            value = m.group(group_idx)
                            meta[field_name] = apply_transform(value, extraction.transform)
                        except Exception:
                            pass
        except Exception:
            pass
    elif mapping.validity_period:
        # single-capture validity_period
        try:
            meta["validity_period"] = _get(mapping.validity_period)
        except Exception:
            pass

    # geographic_scope peut aussi venir de validity_period
    if mapping.geographic_scope:
        try:
            meta["geographic_scope"] = _get(mapping.geographic_scope)
        except Exception:
            pass

    return FileMetadataPivot(**{k: v for k, v in meta.items() if v is not None and k in FileMetadataPivot.model_fields})


# ─────────────────────────────────────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────────────────────────────────────

def _parse_row_range(rows_spec: str) -> tuple[int, int]:
    parts = rows_spec.split(":")
    if len(parts) != 2:
        raise ParsingError(f"Format data_rows invalide : '{rows_spec}'")
    return int(parts[0]), int(parts[1])


def _first_col_of_zone(cols_spec: str) -> str:
    """Extrait la première lettre de colonne depuis 'A:G' → 'A'."""
    return cols_spec.split(":")[0].strip()


def _get_cell(row: Row, col: str) -> str | None:
    idx = col_letter_to_idx(col)
    val = row[idx] if idx < len(row) else None
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    return str(val).strip()


def _sheet_name_or_none(sheet_match: str | dict[str, str]) -> str | None:
    if isinstance(sheet_match, str) and sheet_match not in ("auto",):
        return sheet_match
    return None
