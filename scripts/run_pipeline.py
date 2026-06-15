"""CLI : exécute le pipeline complet (parsing -> delta -> export Gery) sur un fichier réel.

Usage :
    uv run python scripts/run_pipeline.py <fichier.xlsx> --supplier <code_fournisseur>

Le code fournisseur correspond au nom du fichier YAML dans config/suppliers/
(ex: atlantic_scga_chauffage -> config/suppliers/atlantic_scga_chauffage_v1.yaml).

Aucun état connu n'est utilisé : le delta simule une première ingestion (tout en CREATE).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from middleware.core.exceptions import MappingValidationError, ParsingError
from middleware.delta.engine import compute_delta
from middleware.exporter.gery import generate_gery_exports
from middleware.parser.matrix_extractor import parse_matrix_file
from middleware.parser.multi_table_extractor import parse_multi_table_file
from middleware.parser.table_extractor import parse_table_file
from middleware.parser.yaml_loader import load_mapping_rule

CONFIG_DIR = Path("config/suppliers")

PARSERS = {
    "table": parse_table_file,
    "matrix": parse_matrix_file,
    "multi_table": parse_multi_table_file,
}


def _find_mapping_rule(supplier_code: str) -> Path:
    candidates = sorted(CONFIG_DIR.glob(f"{supplier_code}_v*.yaml"), reverse=True)
    if not candidates:
        raise SystemExit(f"Aucun mapping trouvé pour '{supplier_code}' dans {CONFIG_DIR}")
    return candidates[0]


def main() -> None:
    # Évite les crashs sur les noms de fichiers accentués (console Windows en cp1252)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    parser = argparse.ArgumentParser(
        description="Pipeline middleware : parsing -> delta -> export Gery"
    )
    parser.add_argument("excel_file", type=Path, help="Fichier Excel fournisseur")
    parser.add_argument(
        "--supplier", required=True, help="Code fournisseur (ex: atlantic_scga_chauffage)"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("exports"),
        help="Dossier de sortie des exports Gery (défaut: exports/)",
    )
    args = parser.parse_args()

    if not args.excel_file.exists():
        raise SystemExit(f"Fichier introuvable : {args.excel_file}")

    rule_path = _find_mapping_rule(args.supplier)
    print(f"Mapping : {rule_path}")

    try:
        rule = load_mapping_rule(rule_path)
    except MappingValidationError as exc:
        raise SystemExit(f"Mapping invalide : {exc}") from exc

    parse_fn = PARSERS[rule.extraction_mode]
    try:
        result = parse_fn(args.excel_file, rule)
    except ParsingError as exc:
        raise SystemExit(f"Erreur de parsing : {exc}") from exc

    print(f"\nFichier : {args.excel_file.name}")
    print(f"Mode    : {rule.extraction_mode}")
    print(f"Produits extraits : {len(result.products)}")
    print(f"Erreurs de parsing : {result.error_count}")
    print(
        f"Validité : {result.file_metadata.validity_start} -> "
        f"{result.file_metadata.validity_end}"
    )

    if not result.products:
        print("\nAucun produit extrait, arrêt.")
        return

    # Pas d'état connu -> simule une première ingestion (tout en CREATE)
    delta = compute_delta(result.products, known_hashes={})

    print("\n--- Delta (simulation 1ère ingestion) ---")
    print(f"  CREATE        : {len(delta.creates)}")
    print(f"  UPDATE        : {len(delta.updates)}")
    print(f"  PRICE_CHANGE  : {len(delta.price_changes)}")
    print(f"  DELETE        : {len(delta.deletes)}")
    print(f"  REACTIVATE    : {len(delta.reactivates)}")
    print(f"  UNCHANGED     : {delta.unchanged}")

    gery = generate_gery_exports(
        delta,
        rule.gery_export,
        rule.supplier_code,
        args.output_dir,
        result.file_metadata.validity_start,
        result.file_metadata.validity_end,
    )

    print("\n--- Export Gery ---")
    if not gery.files:
        if not rule.gery_export.enabled:
            print(f"  (export désactivé : {rule.gery_export.blocked_reason})")
        else:
            print("  (aucun fichier généré)")
    for f in gery.files:
        print(f"  {f.kind} -> {f.path} ({f.line_count} lignes)")


if __name__ == "__main__":
    main()
