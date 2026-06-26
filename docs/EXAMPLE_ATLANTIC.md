# Exemple complet — Atlantic SCGA (table simple, cas réel)

Ce YAML est le mapping **validé et fonctionnel** pour les fichiers Atlantic SCGA.
Il sert de référence pour tout nouveau fichier Atlantic ou tout fournisseur de structure similaire.

---

## Le YAML complet

```yaml
supplier_code: "atlantic_scga_chauffage"
mapping_version: 1
description: "Atlantic SCGA — gamme chauffage électrique et sèche-serviettes"
upload_mode: "full"
sharepoint_folder: "atlantic"
filename_keywords: ["Chauffage", "chauffage"]

sheet_match: "Atlantic 2026"
header_detection:
  mode: explicit
  row: 9
data_starts_row: 10

extraction_mode: table

row_filter:
  must_have_value_in: ["B"]
  exclude_if_starts_with: ["TARIF GROUPE", "Cet accord"]

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
    constant: "Chauffage électrique"

prices:
  - type: "public"
    source_col: "E"
    transform: "parse_decimal_fr"
    currency: "EUR"
  - type: "installer"
    source_col: "F"
    transform: "parse_decimal_fr"
    currency: "EUR"

attributes:
  - key: "quantity_pack"
    source_col: "D"
    data_type: "integer"
  - key: "base_variant"
    source_col: "A"
    data_type: "enum"
    enum_values: ["B", "V"]

file_metadata:
  validity_start:
    cell: "C4"
    transform: "parse_date_iso"
  validity_end:
    cell: "C5"
    transform: "parse_date_iso"
  contract_reference:
    regex: 'Référence Atlantic (\d+)'
    in_cell: "E7"
  geographic_scope:
    cell: "C3"
  organizational_scope:
    cell: "C2"
  ramery_generic_code:
    cell: "A8"
    transform: "extract_integer"

gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  derived_code_template: "{supplier_product_code}"
  defaults:
    item_purchase_type: "Catalogue"
    minimum_quantity: 1
    code_tva: "TVA20"
    unit_of_measure: "U"
    purchase_type: "Direct"
    gen_prod_posting_group: ""
    job_cost_code: ""
    tree_code: ""
    master_code: ""
    item_category_code: ""
    product_group_code: ""
  price_export_mapping:
    direct_unit_cost: "installer"
```

---

## Explication section par section

### Identité du fournisseur

```yaml
supplier_code: "atlantic_scga_chauffage"
sharepoint_folder: "atlantic"
filename_keywords: ["Chauffage", "chauffage"]
```

- `supplier_code` : identifiant unique en snake_case — ne jamais le changer après validation.
- `sharepoint_folder` : nom exact du dossier SharePoint surveillé par le watcher.
- `filename_keywords` : mots-clés optionnels pour différencier plusieurs fichiers dans le même dossier (ici, distingue les fichiers chauffage des fichiers eau chaude).

---

### Structure du fichier

```yaml
sheet_match: "Atlantic 2026"
header_detection:
  mode: explicit
  row: 9
data_starts_row: 10
```

- `sheet_match` : nom exact de l'onglet Excel. Si le nom change (ex. "Atlantic 2027"), mettre `"auto"` pour que le système prenne l'onglet le plus rempli.
- `row: 9` : la ligne des en-têtes de colonnes (Code article, Désignation…).
- `data_starts_row: 10` : la première ligne de données produit.

---

### Filtrer les lignes parasites

```yaml
row_filter:
  must_have_value_in: ["B"]
  exclude_if_starts_with: ["TARIF GROUPE", "Cet accord"]
```

- `must_have_value_in: ["B"]` : garde uniquement les lignes où la colonne B (code article) est remplie. Élimine les lignes vides et les sous-totaux.
- `exclude_if_starts_with` : ignore les lignes dont le premier contenu commence par ces textes (titres de section, mentions légales…).

---

### Colonnes produit

```yaml
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
    constant: "Chauffage électrique"
```

- `transform: ["strip", "to_uppercase"]` : on peut chaîner plusieurs transforms dans une liste — ici, on nettoie les espaces ET on met en majuscules.
- `constant` : valeur fixe pour tous les produits, pas lue depuis le fichier.
- `required: true` : si une de ces colonnes est vide, la ligne est rejetée (pas d'article sans code ni désignation).

---

### Prix

```yaml
prices:
  - type: "public"
    source_col: "E"
    transform: "parse_decimal_fr"
  - type: "installer"
    source_col: "F"
    transform: "parse_decimal_fr"
```

- `parse_decimal_fr` : pour les prix écrits à la française avec virgule décimale et espace comme séparateur de milliers (ex. `1 234,56`).
- Le `type` sert de nom de référence — on précise ensuite lequel exporter vers Gery avec `price_export_mapping.direct_unit_cost: "installer"`.

---

### Attributs techniques

```yaml
attributes:
  - key: "quantity_pack"
    source_col: "D"
    data_type: "integer"
  - key: "base_variant"
    source_col: "A"
    data_type: "enum"
    enum_values: ["B", "V"]
```

- Les attributs ne vont pas directement dans le CSV Gery mais enrichissent la fiche produit en base.
- `data_type: "enum"` + `enum_values` : valide que la valeur est bien dans la liste.

---

### Métadonnées du cartouche — le point le plus délicat

```yaml
file_metadata:
  validity_start:
    cell: "C4"
    transform: "parse_date_iso"
  validity_end:
    cell: "C5"
    transform: "parse_date_iso"
  contract_reference:
    regex: 'Référence Atlantic (\d+)'
    in_cell: "E7"
  geographic_scope:
    cell: "C3"
  organizational_scope:
    cell: "C2"
  ramery_generic_code:
    cell: "A8"
    transform: "extract_integer"
```

**Lecture directe d'une cellule :**
```yaml
validity_start:
  cell: "C4"
  transform: "parse_date_iso"
```
Lit la cellule C4 et la convertit en date. Si la date est au format JJ/MM/AAAA, utiliser `parse_date_fr` à la place.

**Extraction par regex :**
```yaml
contract_reference:
  regex: 'Référence Atlantic (\d+)'
  in_cell: "E7"
```
Cherche le pattern dans le contenu de la cellule E7 et extrait le groupe capturé entre parenthèses.
> ⚠️ **Règle importante sur les guillemets dans les regex**
> Utilise toujours des **guillemets simples `'...'`** pour les regex, jamais des guillemets doubles.
> Avec des guillemets doubles, il faudrait écrire `"\\d+"` (double backslash) — et si tu copie-colles ce YAML depuis une interface web, le `\\` peut devenir `\`, ce qui rend le YAML invalide.
> Avec des guillemets simples, écris directement `'\d+'` — aucune interprétation des backslashes.

**Code article générique Ramery :**
```yaml
ramery_generic_code:
  cell: "A8"
  transform: "extract_integer"
```
La cellule A8 contient un texte comme *"Code article Ramery 1750"*. `extract_integer` extrait automatiquement le dernier nombre entier du texte → `"1750"`. Ce code ira dans la colonne **"Article générique associé"** du fichier Gery.

---

### Export Gery

```yaml
gery_export:
  enabled: true
  derived_code_template: "{supplier_product_code}"
  price_export_mapping:
    direct_unit_cost: "installer"
```

- `derived_code_template: "{supplier_product_code}"` : le "Code article Frns" exporté = le code article fournisseur brut (colonne B). Pour un code composite, on pourrait écrire `"{supplier_product_code}-{base_variant}"`.
- `direct_unit_cost: "installer"` : le prix installateur (colonne F) part dans la colonne "Direct Unit Cost" de Gery.
- Les champs `defaults` avec des valeurs vides (`""`) sont des placeholders pour les colonnes Gery qui seront remplies manuellement ou par un ETL en aval.

---

## Adapter ce YAML à un autre fichier Atlantic

Si un nouveau fichier Atlantic arrive avec **la même structure** :
1. Charge ce YAML via le menu déroulant "📂 Charger un YAML existant"
2. Vérifie uniquement : `sheet_match` (nom de l'onglet), `data_starts_row`, et les cellules du cartouche
3. Ajuste `filename_keywords` si nécessaire pour le routing automatique
4. Recalcule l'aperçu et valide

Si la **structure des colonnes change** (ex. le prix installateur passe de F à G) :
- Change `source_col` dans `prices` et/ou `columns` accordingly
