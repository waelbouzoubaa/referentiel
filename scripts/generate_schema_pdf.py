"""Génère docs/SCHEMA_BD.pdf : schéma de modélisation de la base (diagramme + référence des colonnes).

Usage : uv run --with matplotlib python scripts/generate_schema_pdf.py
Source de vérité : src/middleware/db/models.py — à régénérer si le schéma change.
"""

from __future__ import annotations

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUTPUT = "docs/SCHEMA_BD.pdf"

# (nom, description, [(colonne, type, flags), ...])
TABLES: dict[str, dict] = {
    "suppliers": {
        "desc": "Référentiel des fournisseurs connus",
        "cols": [
            ("id", "uuid", "PK"),
            ("code", "string", "UQ, NN"),
            ("name", "string", "NN"),
            ("sage_supplier_code", "string", ""),
            ("sharepoint_folder", "string", "UQ, NN"),
            ("upload_mode", "string", "NN, full|incremental"),
            ("active", "bool", "NN"),
            ("notes", "text", ""),
            ("created_at / updated_at", "datetime", "NN"),
        ],
    },
    "mapping_rules": {
        "desc": "YAML de mapping versionné, 1 version active / fournisseur",
        "cols": [
            ("id", "uuid", "PK"),
            ("supplier_id", "uuid", "FK -> suppliers.id"),
            ("version", "int", "NN, UQ avec supplier_id"),
            ("yaml_content", "text", "NN"),
            ("yaml_hash", "string(64)", "UQ, NN"),
            ("active", "bool", "NN"),
            ("validated_by / validated_at", "string / datetime", ""),
            ("created_at", "datetime", "NN"),
        ],
    },
    "supplier_files": {
        "desc": "Fichier Excel reçu depuis SharePoint, archivé et tracé",
        "cols": [
            ("id", "uuid", "PK"),
            ("supplier_id", "uuid", "FK -> suppliers.id"),
            ("filename", "string", "NN"),
            ("sharepoint_item_id / etag", "string", "NN / -"),
            ("content_hash", "string(64)", "UQ, NN (idempotence)"),
            ("size_bytes", "bigint", "NN"),
            ("gcs_path", "string", "NN (chemin temp local)"),
            ("minio_path", "string", "archivage objet (bucket/.../hash_uuid.xlsx)"),
            ("status", "string", "NN, received|processing|processed|failed|skipped"),
            ("received_at / processing_started_at / processing_ended_at", "datetime", ""),
            ("error_message", "text", ""),
            ("validity_start / validity_end", "date", "période de validité tarifs"),
            ("contract_reference / geographic_scope / organizational_scope", "string", ""),
            ("mapping_rule_id", "uuid", "FK -> mapping_rules.id"),
            ("raw_metadata", "jsonb", "métadonnées brutes extraites du fichier"),
        ],
    },
    "products": {
        "desc": "Entité pivot principale : article physique ou prestation",
        "cols": [
            ("id", "uuid", "PK"),
            ("supplier_id", "uuid", "FK -> suppliers.id"),
            ("supplier_product_code", "string", "NN, UQ avec supplier_id"),
            ("generic_code", "string", "code article générique Gery"),
            ("designation", "string", "NN"),
            ("family / subfamily", "string", ""),
            ("product_kind", "string", "NN, physical|service"),
            ("status", "string", "NN, active|inactive|deleted"),
            ("first_seen_in_file_id", "uuid", "FK -> supplier_files.id"),
            ("last_seen_in_file_id", "uuid", "FK -> supplier_files.id"),
            ("business_hash", "string(64)", "NN (détection delta, avec prix)"),
            ("business_hash_no_prices", "string(64)", "NN (détection delta, sans prix)"),
            ("created_at / updated_at", "datetime", "NN"),
        ],
    },
    "product_variants": {
        "desc": "Déclinaisons d'un produit (ex: couleur ALU/BLANC)",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE)"),
            ("variant_dimension", "string", "NN (ex: couleur)"),
            ("variant_value", "string", "NN (ex: ALU)"),
            ("variant_code", "string", "NN, UQ avec product_id+dimension"),
            ("display_order", "int", "NN"),
            ("created_at", "datetime", "NN"),
        ],
    },
    "product_attributes": {
        "desc": "Caractéristiques techniques clé/valeur typées",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE)"),
            ("attribute_key", "string", "NN, UQ avec product_id"),
            ("attribute_value", "text", "NN"),
            ("data_type", "string", "NN, string|integer|decimal|enum|duration|boolean"),
            ("unit", "string", "ex: h, mm"),
            ("created_at", "datetime", "NN"),
        ],
    },
    "prices": {
        "desc": "Prix avec contexte tarifaire complet",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE)"),
            ("variant_id", "uuid", "FK -> product_variants.id (CASCADE, nullable)"),
            ("price_type", "string", "NN (ex: forfait, unitaire)"),
            ("amount", "numeric(15,4)", "NN, >= 0"),
            ("currency", "string(3)", "NN, défaut EUR"),
            ("tier_label / tier_min_quantity / tier_max_quantity / tier_unit", "mixte", "palier de prix"),
            ("valid_from / valid_to", "date", "valid_from <= valid_to"),
            ("source_file_id", "uuid", "FK -> supplier_files.id"),
            ("created_at", "datetime", "NN"),
        ],
    },
    "commercial_rules": {
        "desc": "Règles commerciales (franco port, conditionnement min., etc.)",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE, nullable)"),
            ("supplier_file_id", "uuid", "FK -> supplier_files.id (CASCADE, nullable)"),
            ("rule_type", "string", "NN"),
            ("threshold_value / threshold_unit", "numeric / string", ""),
            ("description / raw_text", "text", ""),
            ("created_at", "datetime", "NN"),
        ],
    },
    "product_history": {
        "desc": "Journal des événements métier par produit",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE)"),
            ("change_type", "string", "NN, CREATE|UPDATE|PRICE_CHANGE|DELETE|REACTIVATE"),
            ("source_file_id", "uuid", "FK -> supplier_files.id"),
            ("detected_at", "datetime", "NN"),
            ("exported_at", "datetime", "rempli quand exporté vers Gery"),
            ("exported_in_id", "uuid", "FK -> gery_exports.id (SET NULL)"),
        ],
    },
    "product_audit": {
        "desc": "Un enregistrement par champ modifié (sans avant/après)",
        "cols": [
            ("id", "uuid", "PK"),
            ("product_id", "uuid", "FK -> products.id (CASCADE)"),
            ("field_name", "string", "NN (ex: price, designation, attr_xxx)"),
            ("changed_at", "datetime", "NN"),
            ("source_file_id", "uuid", "FK -> supplier_files.id"),
        ],
    },
    "gery_exports": {
        "desc": "Trace d'un fichier d'export Gery généré",
        "cols": [
            ("id", "uuid", "PK"),
            ("export_kind", "string", "NN, NEW_ARTICLE|FRNS_CREATE|FRNS_PRICE_UPDATE"),
            ("generated_at / delivered_at", "datetime", ""),
            ("output_path", "string", "NN"),
            ("output_hash", "string(64)", "NN"),
            ("line_count", "int", "NN"),
            ("status", "string", "NN, generated|delivered|acknowledged|failed"),
            ("ack_message", "text", ""),
        ],
    },
    "gery_export_lines": {
        "desc": "Traçabilité ligne par ligne d'un export Gery",
        "cols": [
            ("id", "uuid", "PK"),
            ("export_id", "uuid", "FK->gery_exports.id, UQ+line_number"),
            ("product_id", "uuid", "FK -> products.id (RESTRICT)"),
            ("variant_id", "uuid", "FK -> product_variants.id (RESTRICT, nullable)"),
            ("price_id", "uuid", "FK -> prices.id (RESTRICT, nullable)"),
            ("derived_code", "string", "NN"),
            ("payload", "jsonb", "NN (contenu de la ligne exportée)"),
            ("line_number", "int", "NN"),
        ],
    },
    "mapping_suggestions": {
        "desc": "Suggestions LLM de mapping pour fichiers inconnus (phase 2)",
        "cols": [
            ("id", "uuid", "PK"),
            ("supplier_file_id", "uuid", "FK -> supplier_files.id (CASCADE)"),
            ("suggested_yaml", "text", "NN"),
            ("confidence_avg", "numeric(5,4)", "NN, 0..1"),
            ("field_confidences", "jsonb", "NN"),
            ("warnings", "jsonb", ""),
            ("status", "string", "NN, pending|approved|rejected|modified"),
            ("reviewed_by / reviewed_at", "string / datetime", ""),
            ("approved_yaml", "text", ""),
            ("resulting_mapping_rule_id", "uuid", "FK -> mapping_rules.id (SET NULL)"),
            ("created_at", "datetime", "NN"),
        ],
    },
    "processing_errors": {
        "desc": "Erreurs ligne par ligne, jamais bloquantes au niveau batch",
        "cols": [
            ("id", "uuid", "PK"),
            ("supplier_file_id", "uuid", "FK -> supplier_files.id (CASCADE)"),
            ("row_number", "int", ""),
            ("error_type", "string", "NN, parse|validation|mapping|transform"),
            ("error_field", "string", ""),
            ("error_detail", "text", "NN"),
            ("raw_value", "text", ""),
            ("created_at", "datetime", "NN"),
        ],
    },
}

# Disposition du diagramme : (table, x, y)
LAYOUT: list[tuple[str, float, float]] = [
    ("suppliers", 0, 9),
    ("mapping_rules", 0, 6.5),
    ("mapping_suggestions", 0, 1.5),
    ("processing_errors", 0, -1),

    ("supplier_files", 4.6, 9),

    ("products", 9.2, 9),
    ("product_variants", 9.2, 6.5),
    ("product_attributes", 9.2, 4),
    ("prices", 9.2, 1.5),
    ("commercial_rules", 9.2, -1),

    ("product_history", 13.8, 9),
    ("product_audit", 13.8, 6.5),
    ("gery_exports", 13.8, 4),
    ("gery_export_lines", 13.8, 1.5),
]

# Relations (table_origine -> table_cible) pour les flèches du diagramme
RELATIONS: list[tuple[str, str]] = [
    ("mapping_rules", "suppliers"),
    ("supplier_files", "suppliers"),
    ("supplier_files", "mapping_rules"),
    ("products", "suppliers"),
    ("products", "supplier_files"),
    ("product_variants", "products"),
    ("product_attributes", "products"),
    ("prices", "products"),
    ("prices", "product_variants"),
    ("prices", "supplier_files"),
    ("commercial_rules", "products"),
    ("commercial_rules", "supplier_files"),
    ("product_history", "products"),
    ("product_history", "supplier_files"),
    ("product_history", "gery_exports"),
    ("product_audit", "products"),
    ("product_audit", "supplier_files"),
    ("gery_export_lines", "gery_exports"),
    ("gery_export_lines", "products"),
    ("gery_export_lines", "product_variants"),
    ("gery_export_lines", "prices"),
    ("mapping_suggestions", "supplier_files"),
    ("mapping_suggestions", "mapping_rules"),
    ("processing_errors", "supplier_files"),
]

BOX_W, BOX_H = 3.9, 1.5


def draw_diagram(pdf: PdfPages) -> None:
    fig, ax = plt.subplots(figsize=(22, 13))
    positions = {name: (x, y) for name, x, y in LAYOUT}

    for name, x, y in LAYOUT:
        box = FancyBboxPatch(
            (x, y), BOX_W, BOX_H,
            boxstyle="round,pad=0.05",
            linewidth=1.2, edgecolor="#2b5b84", facecolor="#eaf2fa",
        )
        ax.add_patch(box)
        ax.text(x + BOX_W / 2, y + BOX_H - 0.32, name, ha="center", va="top",
                fontsize=10, fontweight="bold", family="monospace")
        ax.text(x + BOX_W / 2, y + 0.4, TABLES[name]["desc"], ha="center", va="center",
                fontsize=7, wrap=True, style="italic")
        ax.text(x + BOX_W / 2, y + 0.1, f"{len(TABLES[name]['cols'])} colonnes",
                ha="center", va="center", fontsize=6.5, color="#555555")

    for src, dst in RELATIONS:
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        # point de départ/arrivée : côté le plus proche
        start = (x1 + BOX_W / 2, y1 + BOX_H / 2)
        end = (x2 + BOX_W / 2, y2 + BOX_H / 2)
        arrow = FancyArrowPatch(
            start, end,
            connectionstyle="arc3,rad=0.08",
            arrowstyle="-|>", mutation_scale=8,
            color="#999999", linewidth=0.6,
            shrinkA=35, shrinkB=35, zorder=0,
        )
        ax.add_patch(arrow)

    ax.set_xlim(-0.5, 18.5)
    ax.set_ylim(-2, 11)
    ax.axis("off")
    ax.set_title(
        "Middleware Ramery — Schéma de la base de données (14 tables)\n"
        "Flèches : relation de clé étrangère (table → table référencée)",
        fontsize=13, fontweight="bold",
    )
    pdf.savefig(fig)
    plt.close(fig)


def draw_reference_pages(pdf: PdfPages) -> None:
    items = list(TABLES.items())
    i = 0
    while i < len(items):
        fig, ax = plt.subplots(figsize=(11.7, 16.5))  # A4 portrait
        ax.axis("off")
        y = 1.0
        line_h = 0.0145
        while i < len(items) and y > 0.05:
            name, info = items[i]
            needed = 0.05 + len(info["cols"]) * line_h
            if y - needed < 0 and y < 0.95:
                break
            ax.text(0.02, y, name, fontsize=13, fontweight="bold", family="monospace",
                    transform=ax.transAxes, va="top")
            ax.text(0.98, y, info["desc"], fontsize=9, style="italic", color="#555555",
                    transform=ax.transAxes, va="top", ha="right")
            y -= 0.028
            for col, ctype, flags in info["cols"]:
                ax.text(0.04, y, col, fontsize=8.5, family="monospace",
                        transform=ax.transAxes, va="top")
                ax.text(0.50, y, ctype, fontsize=8.5, family="monospace", color="#1a5276",
                        transform=ax.transAxes, va="top")
                ax.text(0.68, y, flags, fontsize=8.5, family="monospace", color="#7d3c98",
                        transform=ax.transAxes, va="top")
                y -= line_h
            y -= 0.02
            i += 1
        pdf.savefig(fig)
        plt.close(fig)


def main() -> None:
    with PdfPages(OUTPUT) as pdf:
        draw_diagram(pdf)
        draw_reference_pages(pdf)
    print(f"-> {OUTPUT}")


if __name__ == "__main__":
    main()
