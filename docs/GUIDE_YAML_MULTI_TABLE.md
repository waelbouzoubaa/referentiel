# Guide — Remplir un YAML pour un fichier « multi_table » (type Agenor)

Le mode `multi_table` s'utilise quand un seul fichier Excel contient **plusieurs tableaux
distincts** : chaque tableau a sa propre zone, ses propres colonnes, et représente une
famille de produits ou de prestations différente.

> Pour le mode liste plate → voir **Table simple**.
> Pour une grille de prix avec paliers × variantes → voir **Matrix (Airisol)**.

---

## 1. Quand utiliser le mode `multi_table`

Ton fichier ressemble à :

```
Lignes 1-6   : en-tête cartouche (dates, périmètre géographique…)
Lignes 7-17  : Tableau 1 — Entretien bases de vie (matrice 2D : taille × fréquence)
Lignes 18-21 : séparateur / titre
Lignes 22-26 : Tableau 2 — Fournitures consommables (barème simple)
```

→ Chaque tableau doit être décrit séparément dans une liste `tables:`. C'est `extraction_mode: multi_table`.

---

## 2. La structure `tables`

Chaque entrée de la liste `tables:` décrit un tableau :

```yaml
tables:
  - name: "nom_technique"          # identifiant snake_case (pas affiché)
    description: "..."             # libellé lisible (optionnel)
    zone:
      header_row: 7                # n° de la ligne d'en-têtes du tableau
      data_rows: "8:17"            # plage des lignes de données
      cols: "A:G"                  # colonnes du tableau
    layout: "matrix_2D"            # voir ci-dessous
    col_dimensions: [...]          # groupes de colonnes (pour matrix_2D)
    product_template:
      designation_template: "..."  # template du libellé produit
      supplier_product_code_template: "..."  # template du code article
      family: "..."
      subfamily: "..."
    prices: [...]
    attributes: [...]
```

### Valeurs de `layout`

| Layout | Usage |
|---|---|
| `matrix_2D` | Tableau avec dimensions en colonnes (ex. fréquence 1×/2×/5× semaine) — chaque groupe de colonnes = une dimension |
| `barème_1D` | Tableau simple : 1 ligne = 1 produit, prix en colonne B |

---

## 3. YAML complet commenté (exemple réel : Agenor)

```yaml
supplier_code: "agenor"
mapping_version: 1
description: "Agenor — entretien bases vie + fournitures consommables (prestations)"
upload_mode: "full"
sharepoint_folder: "agenor"

sheet_match: "Agenor 2026"    # ici on sait exactement le nom de l'onglet
header_detection:
  mode: explicit
  row: 8
data_starts_row: 9

extraction_mode: multi_table

tables:
  # ── Tableau 1 : entretien bases de vie ────────────────────────────────────
  - name: "entretien_bases_vie"
    description: "Forfait mensuel d'entretien selon taille de base et fréquence"
    zone:
      header_row: 7
      data_rows: "8:17"
      cols: "A:G"
    layout: "matrix_2D"
    col_dimensions:
      - columns: ["B", "C"]          # colonnes B et C → fréquence 1x/semaine
        key: "frequency"
        value: "1x_semaine"
        price_col: "B"
        max_time_col: "C"
      - columns: ["D", "E"]          # fréquence 2x/semaine
        key: "frequency"
        value: "2x_semaine"
        price_col: "D"
        max_time_col: "E"
      - columns: ["F", "G"]          # fréquence 5x/semaine
        key: "frequency"
        value: "5x_semaine"
        price_col: "F"
        max_time_col: "G"
    product_template:
      designation_template: "Entretien base vie {taille_base_vie} — {frequency}"
      supplier_product_code_template: "AGEN-EBV-{taille_base_vie_slug}-{frequency}"
      family: "Entretien"
      subfamily: "Bases de vie"
    prices:
      - type: "forfait"
        source_col: "B"              # colonne du prix pour cette dimension
        transform: "parse_decimal_fr"
        currency: "EUR"
    attributes:
      - key: "max_monthly_time"
        source_col: "C"
        data_type: "duration"
        unit: "h"
        transform: "parse_duration_fr"

  # ── Tableau 2 : fournitures consommables ──────────────────────────────────
  - name: "fournitures_consommables"
    description: "Forfait mensuel selon nombre de personnes"
    zone:
      header_row: 22
      data_rows: "23:26"
      cols: "A:B"
    layout: "barème_1D"
    product_template:
      designation_template: "Fournitures consommables sanitaires — {tranche_personnes}"
      supplier_product_code_template: "AGEN-FCS-{tranche_personnes_slug}"
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

product_kind: "service"    # tous les tableaux → prestations

# ── Métadonnées du cartouche ──────────────────────────────────────────────
file_metadata:
  validity_period:
    regex: "Validité de l'offre\\s*:\\s*(\\d{2}/\\d{2}/\\d{4})\\s*au\\s*(\\d{2}/\\d{2}/\\d{4})"
    in_cell: "C2"                  # regex sur la cellule C2
    captures:
      validity_start: 1            # 1er groupe → date de début
      validity_end: 2              # 2ème groupe → date de fin
    transform: "parse_date_fr"
  geographic_scope:
    cell: "A4"                     # lit la cellule A4 telle quelle

gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  defaults:
    item_purchase_type: "Catalogue"
    minimum_quantity: 1
    code_tva: "TVA20"
    unit_of_measure: "FORFAIT"
  price_export_mapping:
    direct_unit_cost: "forfait"
```

---

## 4. Extraire les dates avec `validity_period` (regex multi-capture)

Quand les deux dates sont dans une même cellule (ex. "Validité de l'offre : 01/01/2026 au 31/12/2026") :

```yaml
file_metadata:
  validity_period:
    regex: "Validité de l'offre\\s*:\\s*(\\d{2}/\\d{2}/\\d{4})\\s*au\\s*(\\d{2}/\\d{2}/\\d{4})"
    in_cell: "C2"
    captures:
      validity_start: 1   # groupe 1 de la regex
      validity_end: 2     # groupe 2
    transform: "parse_date_fr"
```

Quand les dates sont dans deux cellules séparées, utilise simplement deux `cell:` :
```yaml
file_metadata:
  validity_start:
    cell: "C4"
    transform: "parse_date_fr"
  validity_end:
    cell: "C5"
    transform: "parse_date_fr"
```

---

## 5. Code article générique Ramery

Même principe que pour les autres modes :

```yaml
file_metadata:
  ramery_generic_code:
    cell: "B3"
    transform: "extract_integer"   # si le code est dans un texte comme "Réf Ramery 1480"
    # OU
    cell: "B3"                     # si la cellule contient juste le code
```
```yaml
gery_export:
  defaults:
    article_generique: "1480"      # valeur fixe si non présente dans le fichier
```

---

## 6. Pièges fréquents en mode multi_table

1. **`zone.data_rows`** doit exclure la ligne d'en-tête du tableau — erreur classique : mettre `"7:17"` au lieu de `"8:17"`.
2. **`sheet_match`** : si le nom de l'onglet change entre fichiers du même fournisseur (ex. "Agenor 2025" → "Agenor 2026"), mettre `"auto"` est plus robuste que le nom exact.
3. **`col_dimensions`** : l'ordre des dimensions doit correspondre à l'ordre des colonnes dans le fichier (de gauche à droite).
4. **`product_template`** : les variables `{xxx}` dans les templates font référence aux attributs du produit et aux dimensions — ils doivent exister dans `attributes` ou dans les `col_dimensions`.
5. Chaque tableau génère ses propres lignes dans l'export Gery — vérifie l'aperçu pour confirmer que les deux (ou N) tableaux sont bien présents.
