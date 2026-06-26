# Guide — Remplir un YAML pour un fichier « matrix » (type Airisol)

Le mode `matrix` s'utilise quand le fichier est une **grille de prix** : les lignes sont les
produits, mais les colonnes de prix forment une matrice — **paliers de quantité × variantes**
(couleur, taille, finition…). Chaque combinaison donne une ligne dans l'export Gery.

> Pour le mode liste plate → voir **Table simple**.
> Pour plusieurs tableaux distincts dans un fichier → voir **Multi-table (Agenor)**.

---

## 1. Quand utiliser le mode `matrix`

Ton fichier ressemble à :

|  | Désignation (C) | Ép. (E) | R (F) | G (ALU) | H (BLANC) | I (ALU) | J (BLANC) | K (ALU) | L (BLANC) |
|---|---|---|---|---|---|---|---|---|---|
| ligne 8 | *en-tête paliers* | | | 0–500 m² | 0–500 m² | 500–1000 m² | 500–1000 m² | >1000 m² | >1000 m² |
| ligne 9 | *en-tête variantes* | | | ALU | BLANC | ALU | BLANC | ALU | BLANC |
| ligne 10 | Isometal Lambda | 40 | 1.00 | 12,50 | 11,80 | 11,90 | 11,20 | 11,50 | 10,90 |

→ 1 produit × 2 variantes × 3 paliers = **6 lignes Gery**. C'est `extraction_mode: matrix`.

---

## 2. Les blocs spécifiques au mode matrix

### `data_zone` — zone de données
```yaml
data_zone:
  rows: "10:31"               # plage des lignes produit (en-têtes exclus)
  product_columns: "A:F"      # colonnes qui décrivent le produit (code, désignation, attributs)
  price_matrix_columns: "G:L" # colonnes qui contiennent les prix
```

### `price_matrix` — description de la grille
```yaml
price_matrix:
  tier_axis:                  # paliers de quantité (ligne d'en-tête du haut)
    header_row: 8             # n° de ligne où se trouvent les labels des paliers
    type: "quantity_range"    # type : toujours "quantity_range" pour l'instant
    fallback_unit: "m²"
    detect_per_block: true    # lit le label de palier colonne par colonne (recommandé)
  variant_axis:               # variantes (ligne d'en-tête du bas)
    header_row: 9
    dimension_name: "couleur" # nom de la dimension (couleur, taille, finition…)
  column_groups:              # décrit chaque groupe palier + ses colonnes
    - columns: ["G", "H"]
      tier_label: "0-500m²"   # label affiché dans le code article exporté
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
```

### `derived_code_template` — code article exporté
Le template peut utiliser toutes les variables produit **et** les variables de la matrice :

```yaml
gery_export:
  derived_code_template: "{designation} | ep{epaisseur} | R{r_value} | {variant_code} | {tier_label}"
```
Les segments dont une variable est absente sont automatiquement omis → le même template
fonctionne pour des produits avec ou sans variante/palier.

---

## 3. YAML complet commenté (exemple réel : Airisol)

```yaml
supplier_code: "airisol"
mapping_version: 1
description: "Airisol — étanchéité, matrice de prix multi-paliers / multi-variantes"
upload_mode: "full"
sharepoint_folder: "airisol"

sheet_match: "auto"
header_detection:
  mode: explicit
  row: 9
data_starts_row: 10

extraction_mode: matrix

row_filter:
  must_have_value_in: ["C"]           # ligne valide si désignation remplie
  must_have_value_in_any: ["G", "I", "K"]  # et au moins un prix

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
    derived_from: "{designation} | EP{epaisseur}"   # code calculé, pas en colonne
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

# ── Dates de validité + code article générique ──────────────────────────────
file_metadata:
  validity_start:
    cell: "E4"
    transform: "parse_date_iso"
  validity_end:
    cell: "J4"
    transform: "parse_date_iso"
  ramery_generic_code:
    cell: "A6"
    transform: "extract_integer"   # ex. "Code article Ramery 1750" → "1750"

gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  derived_code_template: "{designation} | ep{epaisseur} | R{r_value} | {variant_code} | {tier_label}"
  defaults:
    item_purchase_type: "Catalogue"
    minimum_quantity: 1
    code_tva: "TVA20"
    unit_of_measure: "M2"
  price_export_mapping:
    direct_unit_cost: "list"
```

---

## 4. Code article générique Ramery

Exactement comme en mode table — les mêmes options :

```yaml
file_metadata:
  ramery_generic_code:
    cell: "A6"
    transform: "extract_integer"   # cellule avec texte mélangé
    # OU
    cell: "B2"                     # cellule avec le code seul
    # OU
```
```yaml
gery_export:
  defaults:
    article_generique: "1750"      # valeur fixe si non présente dans le fichier
```

---

## 5. Pièges fréquents en mode matrix

1. **`column_groups` doit lister toutes les colonnes prix** dans le bon ordre — oublier une colonne = prix ignorés.
2. **`variants`** dans chaque groupe : l'ordre doit correspondre à l'ordre des colonnes (G→ALU, H→BLANC).
3. **`detect_per_block: true`** recommandé — sinon le parser lit le label de palier depuis la première colonne du groupe, ce qui peut être vide.
4. **`derived_from`** pour le code article : indispensable si le code n'est pas dans une colonne fixe (Airisol le calcule depuis désignation + épaisseur).
5. Le nombre de lignes Gery générées = produits × variantes × paliers — vérifie l'aperçu pour confirmer.
