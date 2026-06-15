from __future__ import annotations

import os
import re
from pathlib import Path

import openpyxl
from middleware.core.logging import get_logger

logger = get_logger(__name__)

_EXAMPLE_YAML = """\
supplier_code: "mon_fournisseur"
mapping_version: 1
description: "Fournisseur X — gamme produits Y"
upload_mode: "full"

sheet_match: "Tarif 2026"
header_detection:
  mode: explicit
  row: 9
data_starts_row: 10

extraction_mode: table

row_filter:
  must_have_value_in: ["B"]

columns:
  supplier_product_code:
    source_col: "B"
    transform: ["strip", "to_uppercase"]
    required: true
  designation:
    source_col: "C"
    transform: "strip"
    required: true
  family:
    constant: "Famille Produit"

prices:
  - type: "installer"
    source_col: "F"
    transform: "parse_decimal_fr"
    currency: "EUR"

file_metadata:
  validity_start:
    cell: "C4"
    transform: "parse_date_iso"
  validity_end:
    cell: "C5"
    transform: "parse_date_iso"

gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  defaults:
    item_purchase_type: "Catalogue"
    minimum_quantity: 1
    code_tva: "TVA20"
    unit_of_measure: "U"
  price_export_mapping:
    direct_unit_cost: "installer"
"""


def read_excel_preview(file_path: Path, max_rows: int = 30) -> str:
    """Lit les premières lignes de l'Excel et les retourne sous forme de texte tabulé."""
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        lines = []
        for row_idx, row in enumerate(ws.iter_rows(max_row=max_rows, values_only=True), start=1):
            cells = "\t".join(str(c) if c is not None else "" for c in row)
            lines.append(f"Ligne {row_idx:02d}: {cells}")
        wb.close()
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("preview excel échoué", erreur=str(exc))
        return f"(Impossible de lire le fichier : {exc})"


def _call_gemini(prompt: str) -> str:
    """Appelle l'API Gemini et retourne le texte généré."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY non définie dans les variables d'environnement.")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise RuntimeError("Package google-generativeai non installé.") from exc

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(prompt)
    return response.text


def _clean_yaml_output(raw: str) -> str:
    """Retire les balises markdown si Gemini en ajoute."""
    raw = raw.strip()
    raw = re.sub(r"^```ya?ml\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _guess_supplier_code(yaml_text: str, folder_name: str, filename: str) -> str:
    """Extrait le supplier_code du YAML généré, sinon le construit depuis le dossier."""
    match = re.search(r'supplier_code:\s*["\']?([a-z0-9_]+)["\']?', yaml_text)
    if match:
        return match.group(1)
    base = re.sub(r"[^a-z0-9]+", "_", (folder_name or filename).lower()).strip("_")
    return base or "fournisseur_inconnu"


def generate_yaml_from_excel(
    file_path: Path,
    folder_name: str,
    filename: str,
) -> tuple[str, str]:
    """Génère un YAML de mapping via Gemini à partir de la structure de l'Excel.

    Returns:
        (supplier_code_guess, yaml_content)
    """
    preview = read_excel_preview(file_path)

    prompt = f"""Tu es un expert en configuration de middleware ERP et en extraction de données Excel.

Analyse la structure de ce fichier Excel fournisseur et génère un fichier YAML de configuration.

## Format YAML attendu (exemple complet) :

{_EXAMPLE_YAML}

## Règles importantes :
- extraction_mode doit être "table" (1 produit par ligne, colonnes fixes)
- Si le prix varie selon des paliers de quantité ET des variantes → "matrix"
- Si plusieurs tableaux distincts dans le même onglet → "multi_table"
- supplier_code : snake_case unique, ex: "atlantic_scga_eau", "airisol", "mon_fournisseur"
- Les colonnes sont désignées par leur lettre (A, B, C...)
- transform "parse_decimal_fr" pour les prix avec virgule décimale
- transform "parse_date_iso" pour les dates au format YYYY-MM-DD ou JJ/MM/AAAA

## Fichier à analyser :
Nom : {filename}
Dossier SharePoint : {folder_name}

## Premières lignes du fichier (Ligne N: colA\\tcolB\\tcolC...) :
{preview}

Génère UNIQUEMENT le YAML de configuration, sans explications, sans balises markdown."""

    raw = _call_gemini(prompt)
    yaml_text = _clean_yaml_output(raw)
    supplier_code = _guess_supplier_code(yaml_text, folder_name, filename)

    logger.info(
        "yaml généré par IA",
        supplier_guess=supplier_code,
        filename=filename,
        yaml_length=len(yaml_text),
    )
    return supplier_code, yaml_text
