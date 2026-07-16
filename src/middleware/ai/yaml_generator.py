from __future__ import annotations

import os
import re
from pathlib import Path

import openpyxl

from middleware.core.logging import get_logger
from middleware.parser.grammar import TRANSFORMS_VALIDES

logger = get_logger(__name__)

_EXAMPLE_TABLE = """\
supplier_code: "mon_fournisseur"
mapping_version: 1
description: "Fournisseur X — gamme produits Y"
upload_mode: "full"

sheet_match: "auto"
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
  ramery_generic_code:
    cell: "A8"
    transform: "extract_integer"

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

_EXAMPLE_MATRIX = r"""\
supplier_code: "mon_fournisseur_isolation"
mapping_version: 1
description: "Exemple matrix — grille de prix multi-paliers et multi-variantes"
upload_mode: "full"

sheet_match: "auto"
header_detection:
  mode: explicit
  row: 9
data_starts_row: 10

extraction_mode: matrix

row_filter:
  must_have_value_in: ["C"]
  must_have_value_in_any: ["G", "I", "K"]

data_zone:
  rows: "10:31"
  product_columns: "A:F"
  price_matrix_columns: "G:L"

product_columns:
  family:
    source_col: "A"
    transform: "strip"
  subfamily:
    source_col: "B"
    transform: "strip"
  designation:
    source_col: "C"
    transform: "strip"
    required: true
  supplier_product_code:
    derived_from: "{designation} | EP{epaisseur}"
    required: true

attributes:
  - key: "epaisseur"
    source_col: "E"
    data_type: "decimal"
    unit: "mm"
  - key: "r_value"
    source_col: "F"
    data_type: "decimal"
    unit: "m².K/W"

price_matrix:
  tier_axis:
    header_row: 8
    type: "quantity_range"
    fallback_unit: "m²"
    detect_per_block: true
  variant_axis:
    header_row: 9
    dimension_name: "couleur"
  column_groups:
    - columns: ["G", "H"]
      tier_label: "0-500m²"
      variants: ["ALU", "BLANC"]
    - columns: ["I", "J"]
      tier_label: "500-1000m²"
      variants: ["ALU", "BLANC"]
    - columns: ["K", "L"]
      tier_label: ">1000m²"
      variants: ["ALU", "BLANC"]
  price_type: "list"
  currency: "EUR"
  transform: "parse_decimal_fr"

commercial_rules:
  - source_col: "M"
    rule_type: "franco"
    threshold_unit: "m²"
    parse_pattern: "(\\d+)\\s*m²"
    applies_to: "product"

file_metadata:
  validity_start:
    cell: "E4"
    transform: "parse_date_iso"
  validity_end:
    cell: "J4"
    transform: "parse_date_iso"
  ramery_generic_code:
    cell: "A6"
    transform: "extract_integer"

gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  derived_code_template: "{designation} | ep{epaisseur} | {variant_code} | {tier_label}"
  defaults:
    item_purchase_type: "Catalogue"
    minimum_quantity: 1
    code_tva: "TVA20"
    unit_of_measure: "M2"
  price_export_mapping:
    direct_unit_cost: "list"
"""

_EXAMPLE_MULTI_TABLE = r"""\
supplier_code: "mon_fournisseur_prestations"
mapping_version: 1
description: "Exemple multi_table — plusieurs tableaux indépendants dans le même onglet"
upload_mode: "full"

sheet_match: "auto"
header_detection:
  mode: explicit
  row: 8
data_starts_row: 9

extraction_mode: multi_table
product_kind: "service"

tables:
  - name: "entretien_bases_vie"
    description: "Forfait mensuel d'entretien selon taille de base et fréquence"
    zone:
      header_row: 7
      data_rows: "8:17"
      cols: "A:G"
    layout: "matrix_2D"
    col_dimensions:
      - columns: ["B", "C"]
        key: "frequency"
        value: "1x_semaine"
        price_col: "B"
        max_time_col: "C"
      - columns: ["D", "E"]
        key: "frequency"
        value: "2x_semaine"
        price_col: "D"
        max_time_col: "E"
    product_template:
      designation_template: "Entretien base vie {taille_base_vie} — {frequency}"
      supplier_product_code_template: "PREST-EBV-{taille_base_vie_slug}-{frequency}"
      family: "Entretien"
      subfamily: "Bases de vie"
    prices:
      - type: "forfait"
        source_col: "B"
        transform: "parse_decimal_fr"
        currency: "EUR"
    attributes:
      - key: "max_monthly_time"
        source_col: "C"
        data_type: "duration"
        unit: "h"
        transform: "parse_duration_fr"

  - name: "fournitures_consommables"
    description: "Forfait mensuel selon nombre de personnes"
    zone:
      header_row: 22
      data_rows: "23:26"
      cols: "A:B"
    layout: "barème_1D"
    product_template:
      designation_template: "Fournitures consommables — {tranche_personnes}"
      supplier_product_code_template: "PREST-FCS-{tranche_personnes_slug}"
      family: "Consommables"
      subfamily: "Sanitaires"
    prices:
      - type: "forfait"
        source_col: "B"
        transform: "parse_decimal_fr"
        currency: "EUR"
    attributes:
      - key: "tranche_personnes"
        source_col: "A"
        data_type: "string"

file_metadata:
  validity_period:
    regex: "Validité de l'offre\\s*:\\s*(\\d{2}/\\d{2}/\\d{4})\\s*au\\s*(\\d{2}/\\d{2}/\\d{4})"
    in_cell: "C2"
    captures:
      validity_start: 1
      validity_end: 2
    transform: "parse_date_fr"
  ramery_generic_code:
    cell: "B2"
    transform: "extract_integer"

gery_export:
  enabled: false
  blocked_reason: "Modélisation des prestations à valider avec le métier"
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
    model = genai.GenerativeModel("gemini-2.5-flash")
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

    transforms_list = ", ".join(sorted(TRANSFORMS_VALIDES))

    prompt = f"""
Tu es un expert en configuration de middleware ERP et en extraction de données Excel.

Analyse ce fichier Excel fournisseur et génère un YAML de configuration.
Ce YAML est validé par Pydantic : toute clé inconnue ou section incorrecte sera rejetée.
Respecte EXACTEMENT la structure des exemples ci-dessous.

## Étape 1 — Choisis l'extraction_mode (un seul parmi 3) :
- "table"       : 1 produit par ligne, colonnes fixes. Sections requises : columns.
- "matrix"      : prix variant selon 2 axes croisés (palier × variante/couleur).
  Sections requises : data_zone, product_columns, price_matrix.
  price_matrix contient : tier_axis, variant_axis, column_groups.
  NE PAS utiliser la section "columns" en mode matrix.
- "multi_table" : plusieurs tableaux dans le même onglet.
  Section requise : tables (liste de sous-tableaux).

## EXEMPLE COMPLET — extraction_mode: table
{_EXAMPLE_TABLE}

## EXEMPLE COMPLET — extraction_mode: matrix
{_EXAMPLE_MATRIX}

## EXEMPLE COMPLET — extraction_mode: multi_table
{_EXAMPLE_MULTI_TABLE}

## Schéma — clés valides UNIQUEMENT (ne pas inventer d'autres clés) :
- Haut niveau commun (tous modes) : supplier_code, mapping_version, description,
  upload_mode, sharepoint_folder, sheet_match, header_detection, data_starts_row,
  extraction_mode, product_kind, file_metadata, gery_export, row_filter
- Mode table seulement : columns, prices, attributes
- Mode matrix seulement : data_zone, product_columns, attributes, price_matrix,
  commercial_rules
  - price_matrix : tier_axis, variant_axis, column_groups, price_type, currency, transform
  - Clés INTERDITES (n'existent pas) : matrix_prices, segments, price_configs, dimension
- Mode multi_table seulement : tables (SubTable avec zone, layout, col_dimensions,
  product_template, prices, attributes)
- Valeurs transform autorisées UNIQUEMENT : {transforms_list}
- sheet_match : mets "auto" par défaut (le moteur choisit l'onglet le plus rempli).
  N'indique un nom exact QUE si tu es certain du libellé de l'onglet.
- product_kind : "physical" (défaut) ou "service" UNIQUEMENT — aucune autre valeur.
- ColumnMapping : exactement 1 source parmi source_col, constant, derived_from
- gery_export.enabled = false → blocked_reason obligatoire

## Code article générique Ramery (OBLIGATOIRE à chercher) :
Deux formes possibles — regarde d'abord si c'est une COLONNE (une valeur différente par ligne
de produit, ex. en-tête "code ramery article générique" au-dessus des lignes de données), sinon
cherche une cellule UNIQUE dans le cartouche (lignes 1-10 environ, ex. "Code article Ramery 1750").

- **Colonne dédiée (une valeur par produit)** → mappe comme une colonne normale :
    columns:
      generic_code:
        source_col: "A"
- **Cellule cartouche, texte mixte** (ex. "Code article Ramery 1750") :
    file_metadata:
      ramery_generic_code:
        cell: "A8"
        transform: "extract_integer"
- **Cellule cartouche, code seul** (ex. "1750") :
    file_metadata:
      ramery_generic_code:
        cell: "A8"
- Si tu ne trouves ni colonne ni cellule → omets les deux clés (ne l'invente pas).

## SIREN Fournisseur (optionnel — cherche mais n'invente jamais) :
Certains cartouches contiennent le SIREN du fournisseur (9 chiffres, parfois précédé de
"SIREN", "N° SIREN", "SIRET"...). Si tu le trouves clairement :
    siren_fournisseur:
      cell: "C8"
- Si le texte est mélangé (ex. "SIREN : 123456789") : ajoute `transform: "extract_integer"`.
- Si tu ne le trouves PAS dans le fichier → omets simplement la clé (ne l'invente pas,
  ne mets pas de valeur au hasard). C'est normal que ce champ soit absent selon le fournisseur.

## Règle impérative sur les regex dans file_metadata :
Pour toute valeur regex, utilise TOUJOURS des guillemets simples YAML ('...')
sauf si la regex contient elle-même une apostrophe (ex : "l'offre") — dans ce
dernier cas seulement, utilise des guillemets doubles avec \\\\ pour chaque backslash.
Exemples corrects :
  regex: '(\\d+)'           ← guillemets simples, backslash simple
  regex: "l'offre (\\d+)"  ← guillemets doubles car apostrophe, double backslash

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
    return supplier_code, yaml_text, prompt


def edit_yaml_with_ai(current_yaml: str, instruction: str, preview: str = "") -> str:
    """Modifie un YAML de mapping selon une instruction en langage naturel (Gemini).

    Renvoie le YAML complet mis à jour (nettoyé). La validation Pydantic est faite
    par l'appelant.
    """
    transforms_list = ", ".join(sorted(TRANSFORMS_VALIDES))
    prompt = f"""Tu es un expert en mapping YAML (Excel → ERP Gery) pour ce middleware.

Voici le YAML de mapping ACTUEL :
```yaml
{current_yaml}
```

Aperçu du fichier Excel source (Ligne N: colA<TAB>colB...) :
{preview}

Demande de l'utilisateur :
{instruction}

Applique cette demande en modifiant le YAML. Contraintes :
- Garde la même grammaire (ne change pas extraction_mode sauf si demandé, n'invente pas de clés).
- transforms autorisés uniquement : {transforms_list}
- Dates JJ/MM/AAAA → parse_date_fr ; dates AAAA-MM-JJ → parse_date_iso.
- Code article générique : si c'est une colonne (valeur différente par ligne), mappe
  columns.generic_code (source_col). Si c'est une cellule unique du cartouche, utilise
  ramery_generic_code dans file_metadata avec transform "extract_integer" si la cellule
  contient du texte mixte ("Code article Ramery 1750" → "1750"), sans transform si c'est juste un chiffre.
- siren_fournisseur dans file_metadata : optionnel, uniquement si le cartouche le contient
  clairement (n'invente jamais une valeur).
- Pour les regex : guillemets simples ('...') sauf si la regex contient une apostrophe
  (dans ce cas guillemets doubles avec \\\\ pour chaque backslash).

Réponds UNIQUEMENT avec le YAML complet mis à jour, sans explication ni balises markdown."""

    raw = _call_gemini(prompt)
    updated = _clean_yaml_output(raw)
    logger.info("yaml modifié par IA", instruction=instruction[:120], yaml_length=len(updated))
    return updated
