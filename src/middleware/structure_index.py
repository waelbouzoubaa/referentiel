"""Registre des empreintes structurelles des mappings approuvés.

Permet de reconnaître qu'un nouveau fichier partage EXACTEMENT la même
disposition d'en-tête (mêmes libellés, aux mêmes colonnes, à la même ligne)
qu'un fournisseur déjà validé — même si le fichier vient d'un dossier/nom
différent — pour proposer directement ce mapping comme suggestion, sans appel
IA. Comparaison stricte, aucune tolérance : normalisation uniquement sur les
espaces superflus et la casse (voir _normalize) — un libellé manquant, en
trop, ou décalé d'une seule colonne suffit à invalider la correspondance.

Sert aussi de base à l'auto-validation complète (voir `build_auto_validated_rule`
et `verify_file_metadata_presence`, utilisés par `ingest.py`) : au-delà de
l'en-tête, on vérifie que le cartouche (dates, SIREN, code générique...)
s'extrait aussi correctement sur le nouveau fichier avant de sauter la
validation humaine — jamais en comparant les valeurs (qui diffèrent
légitimement d'un fournisseur à l'autre), seulement leur présence/absence.
"""
from __future__ import annotations

import json
from pathlib import Path

from middleware.core.logging import get_logger
from middleware.parser.excel_reader import find_sheet, read_workbook
from middleware.parser.grammar import MappingRule
from middleware.parser.pivot import FileMetadataPivot, ProductPivot
from middleware.parser.transforms import idx_to_col_letter

logger = get_logger(__name__)

INDEX_FILE = Path("config/structure_index.json")
CONFIG_DIR = Path("config/suppliers")

# Nombre de lignes scannées par feuille pour chercher une ligne d'en-tête candidate
# dans un fichier inconnu — les en-têtes réels de ce projet sont toujours dans ce
# périmètre (le plus loin rencontré à ce jour est la ligne 10, cf. Atlantic).
_MAX_SCAN_ROWS = 30

# Une ligne avec trop peu de cellules non vides n'est pas fiable comme empreinte
# (risque de faux positif sur une ligne quasi vide) — ni enregistrée, ni comparée.
_MIN_SIGNATURE_CELLS = 3

# Champs file_metadata dont on vérifie la présence (jamais la valeur) — voir
# verify_file_metadata_presence. Exclut `extra` (dict libre, pas structurel).
_FILE_METADATA_FIELDS = (
    "validity_start",
    "validity_end",
    "contract_reference",
    "geographic_scope",
    "organizational_scope",
    "ramery_generic_code",
    "siren_fournisseur",
)

HeaderSignature = list[dict[str, str]]


def _normalize(value: object) -> str:
    """Aplatit les espaces superflus et uniformise la casse — rien d'autre n'est toléré."""
    return " ".join(str(value).split()).lower()


def _row_signature(row: list) -> HeaderSignature:
    return [
        {"col": idx_to_col_letter(i), "text": _normalize(v)}
        for i, v in enumerate(row)
        if v is not None and str(v).strip() != ""
    ]


def extract_header_signature(file_path: Path, rule: MappingRule) -> HeaderSignature | None:
    """Extrait la signature d'en-tête d'un fichier à la position connue par sa règle.

    None si le mode de détection n'est pas 'explicit', si le fichier/la ligne est
    illisible, ou si la ligne n'a pas assez de cellules non vides — best-effort,
    ne doit jamais faire échouer l'appelant (approbation d'un mapping).
    """
    if rule.header_detection.mode != "explicit" or rule.header_detection.row is None:
        return None
    try:
        sheets = read_workbook(file_path)
        _, sheet = find_sheet(sheets, rule.sheet_match)
    except Exception:
        return None
    row_idx = rule.header_detection.row - 1
    if row_idx < 0 or row_idx >= len(sheet):
        return None
    signature = _row_signature(sheet[row_idx])
    return signature if len(signature) >= _MIN_SIGNATURE_CELLS else None


def _has_value(value: object) -> bool:
    """None ou chaîne vide/blanche = absent. Une cellule vraiment vide peut
    remonter comme '' (pas None) selon le lecteur Excel utilisé — traiter les
    deux cas pareil, sinon une cellule vide compterait comme "présente"."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _file_metadata_present(file_metadata: FileMetadataPivot) -> list[str]:
    """Liste triée des champs file_metadata effectivement extraits (non vides)."""
    return sorted(
        field for field in _FILE_METADATA_FIELDS
        if _has_value(getattr(file_metadata, field, None))
    )


def _generic_code_pattern(products: list[ProductPivot]) -> str:
    """Classe le motif du code générique par produit (colonne `columns.generic_code`,
    PAS le cartouche file_metadata.ramery_generic_code — voir verify_file_metadata_presence
    pour celui-là) :
    - "none" : aucun produit n'a de code générique (colonne absente ou vide partout).
    - "constant" : même valeur non vide sur toutes les lignes qui en ont une (normal —
      un seul code générique appliqué à tout un lot, cas fréquent).
    - "varying" : au moins deux valeurs différentes selon la ligne (vraie colonne
      par-produit, distinctive).
    """
    values = {p.generic_code for p in products if _has_value(p.generic_code)}
    if not values:
        return "none"
    return "constant" if len(values) == 1 else "varying"


def update_fingerprint(
    supplier_code: str,
    file_path: Path,
    rule: MappingRule,
    file_metadata: FileMetadataPivot | None = None,
    products: list[ProductPivot] | None = None,
) -> None:
    """Recalcule et enregistre l'empreinte structurelle d'un fournisseur tout juste approuvé.

    `file_metadata` : résultat de parsing déjà produit par l'appelant (évite un
    second parsing) — sert à mémoriser quels champs de cartouche (SIREN, dates,
    code générique...) s'extraient sur ce fichier, pour l'auto-validation future
    (voir verify_file_metadata_presence). `products` : idem, sert à mémoriser le
    motif du code générique PAR PRODUIT (voir verify_generic_code_pattern) —
    distinct du cartouche, mode table uniquement (colonne `columns.generic_code`).
    Les deux sont optionnels : si absents, rien n'est exigé pour les futurs
    matches sur cette structure (comportement identique à avant ces vérifications).

    Best-effort : une erreur ici est journalisée mais ne remonte jamais — ce n'est
    qu'un raccourci pour les prochains fichiers, pas une étape critique.
    """
    try:
        signature = extract_header_signature(file_path, rule)
        if signature is None:
            return
        index = _load_index()
        index[supplier_code] = {
            "header": signature,
            "file_metadata_present": (
                _file_metadata_present(file_metadata) if file_metadata is not None else []
            ),
            "generic_code_pattern": (
                _generic_code_pattern(products) if products is not None else None
            ),
        }
        _save_index(index)
        logger.info("empreinte structurelle mise à jour", supplier_code=supplier_code)
    except Exception as exc:
        logger.warning(
            "échec mise à jour empreinte structurelle (ignoré)",
            supplier_code=supplier_code,
            erreur=str(exc),
        )


def find_matching_supplier(file_path: Path) -> tuple[str, int] | None:
    """Cherche un fournisseur déjà connu dont l'en-tête correspond EXACTEMENT à une
    ligne du nouveau fichier (mêmes colonnes, mêmes libellés normalisés, même ordre).

    Scanne les premières lignes de chaque feuille du nouveau fichier ; dès qu'une
    ligne correspond mot pour mot à une empreinte connue, retourne
    (supplier_code, numéro de ligne 1-indexé où le match a été trouvé) — ce numéro
    est indispensable à build_auto_validated_rule pour décaler les positions
    (l'en-tête peut être sur une ligne différente d'un fichier à l'autre, même à
    contenu de colonnes identique). None si rien ne correspond (ou en cas d'erreur
    de lecture) — la génération IA prend alors le relais comme avant.
    """
    known = {code: sig for code, sig in _iter_header_signatures(_load_index())}
    if not known:
        return None
    try:
        sheets = read_workbook(file_path)
    except Exception:
        return None

    for sheet in sheets.values():
        for row_idx, row in enumerate(sheet[:_MAX_SCAN_ROWS]):
            row_sig = _row_signature(row)
            if len(row_sig) < _MIN_SIGNATURE_CELLS:
                continue
            for code, known_sig in known.items():
                if row_sig == known_sig:
                    return code, row_idx + 1
    return None


def get_expected_file_metadata_fields(supplier_code: str) -> list[str]:
    """Champs file_metadata attendus (déjà vus) pour ce fournisseur — vide si aucun."""
    entry = _load_index().get(supplier_code)
    if isinstance(entry, dict):
        return entry.get("file_metadata_present", [])
    return []  # ancien format (liste nue) ou fournisseur inconnu — rien d'exigé


def verify_file_metadata_presence(supplier_code: str, file_metadata: FileMetadataPivot) -> bool:
    """True si tous les champs de cartouche attendus pour ce fournisseur s'extraient
    aussi sur ce nouveau fichier. Ne compare jamais les valeurs (un SIREN différent
    d'un fournisseur à l'autre est normal) — seulement la présence/absence.

    Vérification binaire, sans seuil : un seul champ attendu absent invalide
    l'auto-validation complète (repli vers la validation humaine).
    """
    expected = get_expected_file_metadata_fields(supplier_code)
    if not expected:
        return True
    present = set(_file_metadata_present(file_metadata))
    return all(field in present for field in expected)


def verify_generic_code_pattern(supplier_code: str, products: list[ProductPivot]) -> bool:
    """True si le motif du code générique par produit (aucun / même partout / différent
    par ligne — voir _generic_code_pattern) est identique à celui du fournisseur matché.

    Détecte le cas où la colonne code générique a changé de nature entre les deux
    fichiers (ex: vraie colonne par-produit dans le fichier d'origine, mais colonne
    vide ou constante dans le nouveau — signe que la colonne a bougé ou ne correspond
    plus). Ne compare jamais les valeurs elles-mêmes (un code différent d'un fichier à
    l'autre, à motif égal, est normal) — uniquement la classe de motif.
    """
    entry = _load_index().get(supplier_code)
    expected = entry.get("generic_code_pattern") if isinstance(entry, dict) else None
    if expected is None:
        return True  # jamais enregistré (ancien format, ou fournisseur sans colonne code générique)
    return _generic_code_pattern(products) == expected


def _parse_row_range(rows_spec: str) -> tuple[int, int]:
    """Parse '10:31' → (10, 31) (1-based, inclusive)."""
    parts = rows_spec.split(":")
    if len(parts) != 2:
        raise ValueError(f"Format de plage de lignes invalide : '{rows_spec}'")
    return int(parts[0]), int(parts[1])


def build_auto_validated_rule(
    matched_code: str, new_file_path: Path, new_header_row: int
) -> MappingRule | None:
    """Construit une règle adaptée au nouveau fichier à partir du mapping matché.

    `new_header_row` : ligne (1-indexée) où l'en-tête a été retrouvé dans le
    NOUVEAU fichier — donnée par find_matching_supplier. L'en-tête peut être à
    une ligne différente de celle du fichier d'origine (cartouche plus court/
    long au-dessus) même à contenu de colonnes strictement identique : toutes
    les positions du mapping matché sont donc décalées du même écart
    (`new_header_row - rule.header_detection.row`) avant tout autre ajustement
    — sinon la ligne d'en-tête elle-même finirait lue comme une ligne de
    produit (bug constaté en test réel).

    Recalcule ensuite les bornes de FIN de plage (matrix/multi_table) contre la
    VRAIE taille du nouveau fichier au lieu de copier aveuglément celle du
    fichier d'origine — sinon des lignes de produits en plus par rapport au
    fichier d'origine seraient silencieusement ignorées (hors plage copiée).
    - matrix : une seule zone par feuille → borne de fin étendue à la taille
      réelle de la feuille (le row_filter existant élimine déjà les lignes
      vides au-delà des vraies données).
    - multi_table : plusieurs zones empilées sur la même feuille → la borne de
      fin d'un sous-tableau est calée juste avant l'en-tête (décalé) du
      suivant (ou la fin de la feuille pour le dernier), jamais devinée. Tous
      les sous-tableaux sont supposés décalés du MÊME écart — hypothèse
      raisonnable (un cartouche plus long/court en haut de fichier décale tout
      le reste uniformément) mais pas garantie si l'espacement entre tableaux
      varie lui aussi d'un fichier à l'autre.
    - table : seul data_starts_row est décalé, aucune borne de fin à recalculer
      (lit déjà jusqu'à la fin de la feuille).

    Retourne None si le mode de détection n'est pas 'explicit', ou si un
    recalcul sûr n'est pas possible — jamais d'exception qui remonte, l'appelant
    retombe alors sur la suggestion pré-remplie + validation humaine.
    """
    from middleware.parser.yaml_loader import validate_mapping_yaml

    yaml_path = CONFIG_DIR / f"{matched_code}_v1.yaml"
    if not yaml_path.exists():
        return None
    try:
        rule, errors = validate_mapping_yaml(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if rule is None or errors:
        return None
    if rule.header_detection.mode != "explicit" or rule.header_detection.row is None:
        return None

    try:
        sheets = read_workbook(new_file_path)
        _, sheet = find_sheet(sheets, rule.sheet_match)
    except Exception:
        return None
    sheet_len = len(sheet)

    delta = new_header_row - rule.header_detection.row

    try:
        rule = rule.model_copy(update={
            "header_detection": rule.header_detection.model_copy(
                update={"row": new_header_row}
            ),
        })

        if rule.extraction_mode == "table":
            rule = rule.model_copy(update={"data_starts_row": rule.data_starts_row + delta})

        elif rule.extraction_mode == "matrix" and rule.data_zone is not None:
            row_start, _row_end = _parse_row_range(rule.data_zone.rows)
            new_data_zone = rule.data_zone.model_copy(
                update={"rows": f"{row_start + delta}:{sheet_len}"}
            )
            new_price_matrix = rule.price_matrix.model_copy(update={
                "tier_axis": rule.price_matrix.tier_axis.model_copy(
                    update={"header_row": rule.price_matrix.tier_axis.header_row + delta}
                ),
                "variant_axis": rule.price_matrix.variant_axis.model_copy(
                    update={"header_row": rule.price_matrix.variant_axis.header_row + delta}
                ),
            })
            rule = rule.model_copy(update={
                "data_zone": new_data_zone, "price_matrix": new_price_matrix,
            })

        elif rule.extraction_mode == "multi_table" and rule.tables:
            shifted_tables = []
            for sub_table in rule.tables:
                row_start, _row_end = _parse_row_range(sub_table.zone.data_rows)
                # borne de fin recalculée juste après (placeholder = début, le temps
                # de connaître le header_row décalé de tous les sous-tableaux)
                new_zone = sub_table.zone.model_copy(update={
                    "header_row": sub_table.zone.header_row + delta,
                    "data_rows": f"{row_start + delta}:{row_start + delta}",
                })
                shifted_tables.append(sub_table.model_copy(update={"zone": new_zone}))

            new_tables = []
            for i, sub_table in enumerate(shifted_tables):
                row_start, _placeholder_end = _parse_row_range(sub_table.zone.data_rows)
                if i + 1 < len(shifted_tables):
                    new_end = shifted_tables[i + 1].zone.header_row - 1
                else:
                    new_end = sheet_len
                new_zone = sub_table.zone.model_copy(
                    update={"data_rows": f"{row_start}:{new_end}"}
                )
                new_tables.append(sub_table.model_copy(update={"zone": new_zone}))
            rule = rule.model_copy(update={"tables": new_tables})
        # mode table : déjà décalé ci-dessus, aucune borne de fin à recalculer.
    except Exception as exc:
        logger.warning(
            "recalcul de position/plage impossible (repli vers validation humaine)",
            supplier_code=matched_code,
            erreur=str(exc),
        )
        return None

    return rule


def _iter_header_signatures(index: dict) -> list[tuple[str, HeaderSignature]]:
    """Extrait (code, signature_en_tête) de chaque entrée, tolère l'ancien format
    (liste nue sans file_metadata_present)."""
    result = []
    for code, entry in index.items():
        header = entry.get("header") if isinstance(entry, dict) else entry
        if header:
            result.append((code, header))
    return result


def _load_index() -> dict:
    if not INDEX_FILE.exists():
        return {}
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(index: dict) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
