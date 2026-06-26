# Guide — Remplir un YAML pour un fichier « table simple » (type Atlantic)

Ce guide explique comment écrire le fichier de mapping YAML pour un fournisseur dont le
catalogue Excel est une **liste plate** : 1 ligne = 1 produit, colonnes fixes (le cas le plus
courant — Atlantic, etc.). Pas besoin de coder : un YAML = un fournisseur.

> Pour les cas plus complexes (matrice de prix = Airisol, plusieurs tableaux = Agenor), voir
> les fichiers `airisol_v1.yaml` / `agenor_v1.yaml` et demander de l'aide.

---

## 1. Quand utiliser le mode `table`
Quand le fichier ressemble à :

| (A) | Code article (B) | Désignation (C) | Qté (D) | Prix public (E) | Prix installateur (F) |
|---|---|---|---|---|---|
| B | 341073 | ARTICLE … TEST1 | 1 | 146 | 145 |

→ chaque ligne est un produit, les colonnes sont toujours aux mêmes positions. C'est `table`.

---

## 2. Où trouver les positions (colonnes / cellules)
Dans l'app de validation, l'**aperçu Excel à droite** montre une grille avec :
- les **lettres de colonnes** en haut : `A, B, C, D, E, F…`
- les **numéros de lignes** à gauche.

Tu repères ainsi : « le code article est en colonne **B** », « la date de début est en cellule **C4** ».
C'est exactement ce qu'il faut mettre dans le YAML.

---

## 3. Le YAML commenté (exemple réel : Atlantic chauffage)

```yaml
# ── Identité du fournisseur ─────────────────────────────────────────
supplier_code: "atlantic_scga_chauffage"   # identifiant unique, en snake_case
mapping_version: 1
description: "Atlantic SCGA — chauffage électrique"
upload_mode: "full"                          # "full" (catalogue complet) ou "incremental"
sharepoint_folder: ["atlantic", "atlantic chauffage"]  # dossier(s) SharePoint surveillé(s)

# ── Où lire dans le fichier ─────────────────────────────────────────
sheet_match: "auto"          # ⚠️ mets "auto" (prend l'onglet le plus rempli) sauf si tu es sûr du nom exact
header_detection:
  mode: explicit
  row: 9                     # n° de la ligne d'en-têtes (Code article, Désignation…)
data_starts_row: 10          # n° de la 1ʳᵉ ligne de données

extraction_mode: table       # ← mode "liste plate"
product_kind: "physical"     # "physical" (article) ou "service" — défaut "physical"

# ── Filtrer les lignes parasites (totaux, titres…) ──────────────────
row_filter:
  must_have_value_in: ["B"]                 # garde seulement les lignes où la colonne B est remplie
  exclude_if_starts_with: ["TARIF GROUPE"]  # ignore les lignes commençant par ce texte (optionnel)

# ── Les colonnes du produit ─────────────────────────────────────────
columns:
  supplier_product_code:        # OBLIGATOIRE : le code article du fournisseur
    source_col: "B"
    transform: ["strip", "to_uppercase"]
    required: true
  designation:                  # OBLIGATOIRE : le libellé
    source_col: "C"
    transform: "strip"
    required: true
  family:                       # optionnel : ici une valeur fixe pour tous les produits
    constant: "Chauffage électrique"
  # subfamily: { source_col: "..." }   # optionnel

# ── Les prix ─────────────────────────────────────────────────────────
prices:
  - type: "public"
    source_col: "E"
    transform: "parse_decimal_fr"   # prix avec virgule décimale (1 234,56)
    currency: "EUR"
  - type: "installer"
    source_col: "F"
    transform: "parse_decimal_fr"
    currency: "EUR"

# ── Caractéristiques techniques (optionnel) ─────────────────────────
attributes:
  - key: "quantity_pack"
    source_col: "D"
    data_type: "integer"

# ── Dates de validité + infos cartouche (optionnel mais recommandé) ─
file_metadata:
  validity_start:
    cell: "C4"
    transform: "parse_date_iso"   # date AAAA-MM-JJ → iso ; date JJ/MM/AAAA → parse_date_fr
  validity_end:
    cell: "C5"
    transform: "parse_date_iso"

# ── Ce qui part dans le fichier Gery ────────────────────────────────
gery_export:
  enabled: true
  flatten_strategy: "cartesian"
  derived_code_template: "{supplier_product_code}"   # le "Code article Frns" exporté
  defaults:
    unit_of_measure: "U"        # unité Gery
    minimum_quantity: 1
    code_tva: "TVA20"
    item_purchase_type: "Catalogue"
  price_export_mapping:
    direct_unit_cost: "installer"   # quel prix ci-dessus va dans "Direct Unit Cost"
```

---

## 4. Code article générique Ramery (`ramery_generic_code`)

La colonne **"Article générique associé"** du fichier Gery. Trois façons selon le fichier :

### Cas A — cellule avec texte mélangé (ex. "Code article Ramery 1750")
```yaml
file_metadata:
  ramery_generic_code:
    cell: "C7"
    transform: "extract_integer"   # extrait le dernier entier → "1750"
```

### Cas B — cellule avec le code seul (entier ou texte pur)
```yaml
file_metadata:
  ramery_generic_code:
    cell: "A8"    # lit la cellule telle quelle
```

### Cas C — valeur fixe (le fichier ne la contient pas)
```yaml
gery_export:
  defaults:
    article_generique: "1750"   # fallback si file_metadata ne le fournit pas
```

> **Priorité** : `file_metadata.ramery_generic_code` > `defaults.article_generique`

---

## 5. Les `transform` disponibles
À mettre dans `transform:` (un seul, ou une liste `["strip", "to_uppercase"]`) :

| Transform | Usage |
|---|---|
| `strip`, `strip_upper`, `strip_lower` | nettoie les espaces (+ casse) |
| `to_uppercase`, `to_lowercase` | met en MAJ / min |
| `parse_decimal_fr` | prix à la française : `1 234,56` → 1234.56 |
| `parse_decimal_us` | prix à l'américaine : `1,234.56` |
| `parse_date_fr` | date **JJ/MM/AAAA** (ex. 01/02/2024) |
| `parse_date_iso` | date **AAAA-MM-JJ** (ex. 2024-02-01) |
| `parse_duration_fr` | durée (ex. "2h30") |
| `extract_integer` | extrait le dernier entier d'un texte quelconque |

`data_type` d'un attribut : `string`, `integer`, `decimal`, `enum`, `duration`, `boolean`.

---

## 6. Règles d'or (les pièges fréquents)
1. **`sheet_match: "auto"`** par défaut — sinon, si le nom d'onglet ne correspond pas exactement, le traitement échoue.
2. **Dates** : regarde le format dans l'aperçu. `01/02/2024` → **`parse_date_fr`** ; `2024-02-01` → `parse_date_iso`.
3. **Prix** : presque toujours **`parse_decimal_fr`** (virgule décimale).
4. **`supplier_product_code` et `designation` sont obligatoires** (`required: true`) — sans eux, la ligne est rejetée.
5. **Une seule source par colonne** : `source_col` **OU** `constant` **OU** `derived_from`, jamais deux.
6. **Code Fournisseur SAGE** : il ne se met **PAS** dans le YAML. Il vient du fichier `config/sage_codes.csv`.
7. `row_filter.must_have_value_in` sur une colonne toujours remplie (souvent le code article) évite d'attraper les lignes de total/titre.

---

## 7. Procédure de bout en bout
1. Le fichier inconnu arrive → onglet **« En attente »** dans l'app.
2. Écran de validation : **droite** = aperçu grille (A, B, C…), **gauche** = édition YAML.
3. Remplis le YAML (ou utilise le **Formulaire simplifié** ou le bouton **🤖 IA**).
4. L'**aperçu export Gery** se recalcule sous le fichier brut — vérifie codes / prix / dates.
5. **✅ Valider** → YAML enregistré dans `config/suppliers/<supplier_code>_v1.yaml`, fournisseur connu, prochains fichiers traités automatiquement.

---

## 8. Checklist rapide avant de valider
- [ ] `supplier_code` unique et clair
- [ ] `sheet_match: "auto"` (ou bon nom d'onglet)
- [ ] `header_detection.row` et `data_starts_row` corrects
- [ ] `supplier_product_code` + `designation` mappés, `required: true`
- [ ] prix avec `parse_decimal_fr`, `price_export_mapping.direct_unit_cost` pointe le bon prix
- [ ] dates : bon `transform` (fr vs iso)
- [ ] `ramery_generic_code` renseigné si disponible dans le fichier
- [ ] l'aperçu export Gery montre des lignes correctes
