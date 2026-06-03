from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

from middleware.core.logging import get_logger
from middleware.delta.engine import ChangeType, DeltaResult, ProductDelta
from middleware.parser.grammar import GeryExportConfig
from middleware.parser.pivot import PricePivot, ProductPivot, VariantPivot

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Résultat de génération
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GeneratedFile:
    """Fichier Excel Gery généré."""
    kind: str  # NEW_ARTICLE | NEW_ART_FRNS_CREATE | NEW_ART_FRNS_PRICE_UPDATE
    path: Path
    line_count: int
    output_hash: str


@dataclass
class GeryExportResult:
    """Résultat complet de la génération des 3 fichiers Gery."""
    files: list[GeneratedFile] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# Colonnes des 3 fichiers Gery (ordre fixe imposé par l'ERP)
# ─────────────────────────────────────────────────────────────────────────────

NEW_ARTICLE_COLS = [
    "Code article",
    "Désignation",
    "Famille",
    "Sous-famille",
    "Code TVA",
    "Unité de mesure",
    "Type achat article",
    "Quantité minimum",
    "Date début validité",
    "Date fin validité",
]

NEW_ART_FRNS_CREATE_COLS = [
    "Code article",
    "Code fournisseur",
    "Référence fournisseur",
    "Désignation fournisseur",
    "Prix unitaire direct",
    "Devise",
    "Date début validité",
    "Date fin validité",
    "Quantité minimum commande",
    "Unité de mesure",
]

NEW_ART_FRNS_PRICE_UPDATE_COLS = [
    "Code article",
    "Code fournisseur",
    "Référence fournisseur",
    "Nouveau prix unitaire direct",
    "Devise",
    "Date effet",
]


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def generate_gery_exports(
    delta: DeltaResult,
    export_config: GeryExportConfig,
    supplier_code: str,
    output_dir: Path,
    validity_start: date | None = None,
    validity_end: date | None = None,
) -> GeryExportResult:
    """Génère les 3 fichiers Excel d'import Gery depuis un DeltaResult.

    Args:
        delta: Résultat du moteur delta (creates, price_changes, etc.).
        export_config: Configuration Gery depuis le YAML fournisseur.
        supplier_code: Code fournisseur Ramery (pour la colonne "Code fournisseur").
        output_dir: Répertoire de sortie pour les fichiers .xlsx.
        validity_start: Date début validité du catalogue (issue du cartouche).
        validity_end: Date fin validité du catalogue.

    Returns:
        GeryExportResult avec les chemins des fichiers générés.
    """
    if not export_config.enabled:
        logger.info(
            "gery_export désactivé",
            supplier_code=supplier_code,
            raison=export_config.blocked_reason,
        )
        return GeryExportResult()

    output_dir.mkdir(parents=True, exist_ok=True)
    result = GeryExportResult()
    defaults = export_config.defaults
    price_field = export_config.price_export_mapping.direct_unit_cost

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # ── NEW_ARTICLE (créations + réactivations) ───────────────────────────
    new_article_rows = _build_new_article_rows(
        delta.creates + delta.reactivates,
        defaults,
        validity_start,
        validity_end,
    )
    if new_article_rows:
        path = output_dir / f"NEW_ARTICLE_{supplier_code}_{timestamp}.xlsx"
        _write_excel(path, "NEW_ARTICLE", NEW_ARTICLE_COLS, new_article_rows)
        result.files.append(GeneratedFile(
            kind="NEW_ARTICLE",
            path=path,
            line_count=len(new_article_rows),
            output_hash=_file_hash(path),
        ))

    # ── NEW_ART_FRNS_CREATE (créations + réactivations — ligne fournisseur) ──
    frns_create_rows = _build_frns_create_rows(
        delta.creates + delta.reactivates,
        supplier_code,
        defaults,
        price_field,
        validity_start,
        validity_end,
    )
    if frns_create_rows:
        path = output_dir / f"NEW_ART_FRNS_CREATE_{supplier_code}_{timestamp}.xlsx"
        _write_excel(path, "NEW_ART_FRNS_CREATE", NEW_ART_FRNS_CREATE_COLS, frns_create_rows)
        result.files.append(GeneratedFile(
            kind="NEW_ART_FRNS_CREATE",
            path=path,
            line_count=len(frns_create_rows),
            output_hash=_file_hash(path),
        ))

    # ── NEW_ART_FRNS_PRICE_UPDATE (mises à jour de prix) ─────────────────
    price_update_rows = _build_price_update_rows(
        delta.price_changes,
        supplier_code,
        price_field,
    )
    if price_update_rows:
        path = output_dir / f"NEW_ART_FRNS_PRICE_UPDATE_{supplier_code}_{timestamp}.xlsx"
        _write_excel(path, "NEW_ART_FRNS_PRICE_UPDATE", NEW_ART_FRNS_PRICE_UPDATE_COLS, price_update_rows)
        result.files.append(GeneratedFile(
            kind="NEW_ART_FRNS_PRICE_UPDATE",
            path=path,
            line_count=len(price_update_rows),
            output_hash=_file_hash(path),
        ))

    logger.info(
        "exports Gery générés",
        supplier_code=supplier_code,
        fichiers=len(result.files),
        lignes_total=sum(f.line_count for f in result.files),
    )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Construction des lignes par fichier
# ─────────────────────────────────────────────────────────────────────────────

def _build_new_article_rows(
    deltas: list[ProductDelta],
    defaults: dict[str, Any],
    validity_start: date | None,
    validity_end: date | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for delta in deltas:
        if delta.new_product is None:
            continue
        p = delta.new_product
        for derived_code in _get_derived_codes(p, delta):
            rows.append({
                "Code article": derived_code,
                "Désignation": _get_designation(p, delta),
                "Famille": p.family or "",
                "Sous-famille": p.subfamily or "",
                "Code TVA": defaults.get("code_tva", "TVA20"),
                "Unité de mesure": defaults.get("unit_of_measure", "U"),
                "Type achat article": defaults.get("item_purchase_type", "Catalogue"),
                "Quantité minimum": defaults.get("minimum_quantity", 1),
                "Date début validité": validity_start,
                "Date fin validité": validity_end,
            })
    return rows


def _build_frns_create_rows(
    deltas: list[ProductDelta],
    supplier_code: str,
    defaults: dict[str, Any],
    price_field: str,
    validity_start: date | None,
    validity_end: date | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for delta in deltas:
        if delta.new_product is None:
            continue
        p = delta.new_product
        for derived_code, price in _get_derived_codes_with_prices(p, delta, price_field):
            rows.append({
                "Code article": derived_code,
                "Code fournisseur": supplier_code,
                "Référence fournisseur": p.supplier_product_code,
                "Désignation fournisseur": _get_designation(p, delta),
                "Prix unitaire direct": float(price.amount) if price else None,
                "Devise": price.currency if price else "EUR",
                "Date début validité": validity_start,
                "Date fin validité": validity_end,
                "Quantité minimum commande": defaults.get("minimum_quantity", 1),
                "Unité de mesure": defaults.get("unit_of_measure", "U"),
            })
    return rows


def _build_price_update_rows(
    deltas: list[ProductDelta],
    supplier_code: str,
    price_field: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    today = date.today()
    for delta in deltas:
        if delta.new_product is None:
            continue
        p = delta.new_product
        for derived_code, price in _get_derived_codes_with_prices(p, delta, price_field):
            rows.append({
                "Code article": derived_code,
                "Code fournisseur": supplier_code,
                "Référence fournisseur": p.supplier_product_code,
                "Nouveau prix unitaire direct": float(price.amount) if price else None,
                "Devise": price.currency if price else "EUR",
                "Date effet": today,
            })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Flatten strategy : cartesian (produit × variante × palier)
# ─────────────────────────────────────────────────────────────────────────────

def _get_derived_codes(product: ProductPivot, delta: ProductDelta) -> list[str]:
    """Retourne la liste des codes dérivés selon la stratégie flatten=cartesian."""
    if not product.variants:
        return [product.supplier_product_code]

    codes: list[str] = []
    for v_idx, variant in enumerate(product.variants):
        for t_idx, price in enumerate(variant.prices):
            codes.append(
                f"{product.supplier_product_code}-{variant.variant_code}-T{t_idx + 1}"
            )
    return codes or [product.supplier_product_code]


def _get_derived_codes_with_prices(
    product: ProductPivot,
    delta: ProductDelta,
    price_field: str,
) -> list[tuple[str, PricePivot | None]]:
    """Retourne (code_dérivé, prix) pour chaque ligne à exporter."""
    if not product.variants:
        price = _find_price(product.prices, price_field)
        return [(product.supplier_product_code, price)]

    result: list[tuple[str, PricePivot | None]] = []
    for v_idx, variant in enumerate(product.variants):
        for t_idx, price in enumerate(variant.prices):
            if price.price_type == price_field or not price_field:
                code = f"{product.supplier_product_code}-{variant.variant_code}-T{t_idx + 1}"
                result.append((code, price))

    if not result:
        # Fallback : tous les prix des variantes
        for v_idx, variant in enumerate(product.variants):
            for t_idx, price in enumerate(variant.prices):
                code = f"{product.supplier_product_code}-{variant.variant_code}-T{t_idx + 1}"
                result.append((code, price))

    return result or [(product.supplier_product_code, None)]


def _find_price(prices: list[PricePivot], price_type: str) -> PricePivot | None:
    for p in prices:
        if p.price_type == price_type:
            return p
    return prices[0] if prices else None


def _get_designation(product: ProductPivot, delta: ProductDelta) -> str:
    return product.designation


# ─────────────────────────────────────────────────────────────────────────────
# Écriture Excel
# ─────────────────────────────────────────────────────────────────────────────

_HEADER_FILL = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def _write_excel(path: Path, sheet_name: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    # En-têtes
    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL

    # Données
    for row_idx, row_data in enumerate(rows, start=2):
        for col_idx, col_name in enumerate(columns, start=1):
            value = row_data.get(col_name)
            ws.cell(row=row_idx, column=col_idx, value=value)

    # Largeur colonnes automatique (approximatif)
    for col_idx, col_name in enumerate(columns, start=1):
        ws.column_dimensions[
            openpyxl.utils.get_column_letter(col_idx)
        ].width = max(len(col_name) + 2, 16)

    wb.save(path)


def _file_hash(path: Path) -> str:
    """SHA-256 du fichier généré (pour traçabilité)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
