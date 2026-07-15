from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from middleware.core.logging import get_logger
from middleware.delta.engine import DeltaResult, ProductDelta
from middleware.parser.grammar import GeryExportConfig
from middleware.parser.pivot import PricePivot, ProductPivot

logger = get_logger(__name__)

_FIELD_RE = re.compile(r"\{(\w+)\}")

# Normalisation des unités fournisseur → codes Gery
_UOM_MAP: dict[str, str] = {
    "UN": "U", "U": "U",
    "M2": "M2", "M²": "M2",
    "ML": "ML", "M": "M",
    "KG": "KG", "T": "T", "TONNE": "T",
    "L": "L", "M3": "M3", "M³": "M3",
}

# ─────────────────────────────────────────────────────────────────────────────
# Résultat de génération
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RowDetail:
    supplier_product_code: str
    derived_code: str
    payload: dict


@dataclass
class GeneratedFile:
    kind: str
    path: Path
    line_count: int
    output_hash: str
    row_details: list[RowDetail] = field(default_factory=list)


@dataclass
class GeryExportResult:
    files: list[GeneratedFile] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)  # noqa: F841


# ─────────────────────────────────────────────────────────────────────────────
# Colonnes du fichier Gery (ordre imposé par l'ERP)
# ─────────────────────────────────────────────────────────────────────────────

NEW_ARTICLE_COLS = [
    "Code Fournisseur SAGE",
    "Code article Frns",
    "Description",
    "Article générique associé",
    "Unité",
    "Starting Date",
    "Minimum Quantity",
    "Direct Unit Cost",
    "Ending Date",
    "SIREN Fournisseur",
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
    code_fournisseur_sage: str | None = None,
    ramery_generic_code: str | None = None,
    siren_fournisseur: str | None = None,
) -> GeryExportResult:
    if not export_config.enabled:
        logger.info("gery_export désactivé", supplier_code=supplier_code)
        return GeryExportResult()

    # Organise les exports par dossier fournisseur (sharepoint_folder ou supplier_code)
    supplier_dir = output_dir / supplier_code
    supplier_dir.mkdir(parents=True, exist_ok=True)
    result = GeryExportResult()
    defaults = export_config.defaults
    price_field = export_config.price_export_mapping.direct_unit_cost
    code_template = export_config.derived_code_template

    # Fichier d'import unique NEW ARTICLE : créations, réactivations ET lignes
    # modifiées (UPDATE/PRICE_CHANGE). Gery distingue création vs mise à jour à
    # l'import sur la clé d'unicité (Code Fournisseur SAGE + Code article Frns).
    rows, row_details = _build_rows(
        delta.creates + delta.reactivates + delta.updates + delta.price_changes,
        defaults,
        price_field,
        code_template,
        validity_start,
        validity_end,
        code_fournisseur_sage,
        ramery_generic_code,
        siren_fournisseur,
    )

    if rows:
        # Nom horodaté dans le sous-dossier du fournisseur
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        path = supplier_dir / f"NEW_ARTICLE_{supplier_code}_{ts}.csv"
        _write_csv(path, NEW_ARTICLE_COLS, rows)
        result.files.append(GeneratedFile(
            kind="NEW_ARTICLE",
            path=path,
            line_count=len(rows),
            output_hash=_file_hash(path),
            row_details=row_details,
        ))

    logger.info("export Gery généré", supplier_code=supplier_code, lignes=len(rows))
    return result


def build_new_article_rows(
    delta: DeltaResult,
    export_config: GeryExportConfig,
    validity_start: date | None = None,
    validity_end: date | None = None,
    code_fournisseur_sage: str | None = None,
    ramery_generic_code: str | None = None,
    siren_fournisseur: str | None = None,
) -> list[dict[str, Any]]:
    """Construit les lignes NEW_ARTICLE en mémoire (ni fichier ni persistance).

    Sert à l'aperçu de l'export dans l'interface de validation : on voit ce qui
    sortirait pour Gery sans rien enregistrer.
    """
    if not export_config.enabled:
        return []
    rows, _ = _build_rows(
        delta.creates + delta.reactivates + delta.updates + delta.price_changes,
        export_config.defaults,
        export_config.price_export_mapping.direct_unit_cost,
        export_config.derived_code_template,
        validity_start,
        validity_end,
        code_fournisseur_sage,
        ramery_generic_code,
        siren_fournisseur,
    )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Construction des lignes
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_uom(product: ProductPivot, defaults: dict[str, Any]) -> str:
    """Résout l'unité de mesure : attribut produit `unit_of_measure` en priorité, sinon défaut YAML."""
    for attr in product.attributes:
        if attr.key == "unit_of_measure":
            return _UOM_MAP.get(attr.value.strip().upper(), attr.value.strip())
    return defaults.get("unit_of_measure", "U")


def _build_rows(
    deltas: list[ProductDelta],
    defaults: dict[str, Any],
    price_field: str,
    code_template: str | None,
    validity_start: date | None,
    validity_end: date | None,
    code_fournisseur_sage: str | None = None,
    ramery_generic_code: str | None = None,
    siren_fournisseur: str | None = None,
) -> tuple[list[dict[str, Any]], list[RowDetail]]:
    rows: list[dict[str, Any]] = []
    details: list[RowDetail] = []
    for delta in deltas:
        if delta.new_product is None:
            continue
        p = delta.new_product
        for derived_code, price in _get_codes_with_prices(p, price_field, code_template):
            row = {
                "Code Fournisseur SAGE": code_fournisseur_sage,
                "Code article Frns": derived_code,
                "Description": p.designation,
                "Article générique associé": ramery_generic_code or defaults.get("article_generique", ""),
                "Unité": _resolve_uom(p, defaults),
                "Starting Date": validity_start.isoformat() if validity_start else None,
                "Minimum Quantity": defaults.get("minimum_quantity", 1),
                "Direct Unit Cost": float(price.amount) if price else None,
                "Ending Date": validity_end.isoformat() if validity_end else None,
                "SIREN Fournisseur": siren_fournisseur,
            }
            rows.append(row)
            details.append(RowDetail(
                supplier_product_code=p.supplier_product_code,
                derived_code=derived_code,
                payload=row,
            ))
    return rows, details


# ─────────────────────────────────────────────────────────────────────────────
# Génération des codes article
# ─────────────────────────────────────────────────────────────────────────────

def _get_codes_with_prices(
    product: ProductPivot,
    price_field: str,
    code_template: str | None,
) -> list[tuple[str, PricePivot | None]]:
    """Retourne (code_article, prix) pour chaque ligne Gery.

    - Produit matriciel : 1 ligne par variante × palier
    - Produit à paliers sans variante : 1 ligne par palier
    - Produit simple (table mode) : 1 ligne
    Le code est rendu via le template YAML ; si absent, fallback sur supplier_product_code.
    """
    if product.variants:
        result = []
        for variant in product.variants:
            for price in variant.prices:
                if price_field and price.price_type != price_field:
                    continue
                code = _render_code(product, price, variant.variant_code, code_template)
                result.append((code, price))
        return result or [(product.supplier_product_code, None)]

    prices = [p for p in product.prices if not price_field or p.price_type == price_field]
    if not prices:
        prices = product.prices

    if prices:
        return [(_render_code(product, p, None, code_template), p) for p in prices]

    return [(product.supplier_product_code, None)]


def _render_code(
    product: ProductPivot,
    price: PricePivot | None,
    variant_code: str | None,
    template: str | None,
) -> str:
    """Rend le code article depuis le template YAML.

    Le template est découpé par `|`. Chaque segment est omis si une de ses
    variables `{field}` est absente ou vide. Cela rend le template flexible :
    un même template fonctionne pour des produits avec ou sans variante/palier/attributs.

    Exemples :
      template = "{designation} | ep{epaisseur} | R{r_value} | {variant_code} | {tier_label}"
      isometal EP50 ALU → "isometal lambda 0,04 | ep50 | R1.25 | ALU | 0-500m²"
      ISOVAP (pas d'ep/R/variante) → "ISOVAP | 0-720 m²"
      Atlantic (template="{supplier_product_code}") → "341073"
    """
    if not template:
        return product.supplier_product_code

    attrs = {a.key: a.value for a in product.attributes}
    tier_label = _format_tier_label(price) if price else ""

    variables: dict[str, str | None] = {
        "supplier_product_code": product.supplier_product_code,
        "designation": product.designation,
        "family": product.family,
        "subfamily": product.subfamily,
        "variant_code": variant_code,
        "tier_label": tier_label or None,
        **attrs,
    }

    rendered_segments = []
    for segment in template.split("|"):
        segment = segment.strip()
        field_names = _FIELD_RE.findall(segment)
        values = {f: variables.get(f) for f in field_names}
        if any(v is None or v == "" for v in values.values()):
            continue
        rendered = _FIELD_RE.sub(lambda m: str(variables[m.group(1)]), segment)
        if rendered:
            rendered_segments.append(rendered)

    return " | ".join(rendered_segments) or product.supplier_product_code


def _format_tier_label(price: PricePivot) -> str:
    if price.tier_label:
        return price.tier_label.strip()
    if price.tier_min_quantity is None and price.tier_max_quantity is None:
        return ""
    unit = price.tier_unit or ""
    if price.tier_max_quantity is None:
        min_v = int(price.tier_min_quantity) if price.tier_min_quantity == int(price.tier_min_quantity) else price.tier_min_quantity
        return f">{min_v}{unit}"
    min_v = int(price.tier_min_quantity) if price.tier_min_quantity == int(price.tier_min_quantity) else price.tier_min_quantity
    max_v = int(price.tier_max_quantity) if price.tier_max_quantity == int(price.tier_max_quantity) else price.tier_max_quantity
    return f"{min_v}-{max_v}{unit}"


# ─────────────────────────────────────────────────────────────────────────────
# Écriture Excel
# ─────────────────────────────────────────────────────────────────────────────

# Séparateur ';' + BOM UTF-8 : ouverture propre dans Excel FR ; l'intégration
# Gery se fera par un ETL qui fait un UPDATE sur la clé. Format final à confirmer
# avec Rémi (cf. brief §12).
_CSV_DELIMITER = ";"


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=columns, delimiter=_CSV_DELIMITER, extrasaction="ignore"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
