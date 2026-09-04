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
from middleware.parser.pivot import FileMetadataPivot
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


def _file_metadata_present(file_metadata: FileMetadataPivot) -> list[str]:
    """Liste triée des champs file_metadata effectivement extraits (non None)."""
    return sorted(
        field for field in _FILE_METADATA_FIELDS
        if getattr(file_metadata, field, None) is not None
    )


def update_fingerprint(
    supplier_code: str,
    file_path: Path,
    rule: MappingRule,
    file_metadata: FileMetadataPivot | None = None,
) -> None:
    """Recalcule et enregistre l'empreinte structurelle d'un fournisseur tout juste approuvé.

    `file_metadata` : résultat de parsing déjà produit par l'appelant (évite un
    second parsing) — sert à mémoriser quels champs de cartouche (SIREN, dates,
    code générique...) s'extraient sur ce fichier, pour l'auto-validation future
    (voir verify_file_metadata_presence). Optionnel : si absent, aucun champ
    n'est exigé pour les futurs matches sur cette structure (comportement
    identique à avant cette vérification).

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
        }
        _save_index(index)
        logger.info("empreinte structurelle mise à jour", supplier_code=supplier_code)
    except Exception as exc:
        logger.warning(
            "échec mise à jour empreinte structurelle (ignoré)",
            supplier_code=supplier_code,
            erreur=str(exc),
        )


def find_matching_supplier(file_path: Path) -> str | None:
    """Cherche un fournisseur déjà connu dont l'en-tête correspond EXACTEMENT à une
    ligne du nouveau fichier (mêmes colonnes, mêmes libellés normalisés, même ordre).

    Scanne les premières lignes de chaque feuille du nouveau fichier ; dès qu'une
    ligne correspond mot pour mot à une empreinte connue, retourne ce supplier_code.
    None si rien ne correspond (ou en cas d'erreur de lecture) — la génération IA
    prend alors le relais comme avant.
    """
    known = {code: sig for code, sig in _iter_header_signatures(_load_index())}
    if not known:
        return None
    try:
        sheets = read_workbook(file_path)
    except Exception:
        return None

    for sheet in sheets.values():
        for row in sheet[:_MAX_SCAN_ROWS]:
            row_sig = _row_signature(row)
            if len(row_sig) < _MIN_SIGNATURE_CELLS:
                continue
            for code, known_sig in known.items():
                if row_sig == known_sig:
                    return code
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


def _parse_row_range(rows_spec: str) -> tuple[int, int]:
    """Parse '10:31' → (10, 31) (1-based, inclusive)."""
    parts = rows_spec.split(":")
    if len(parts) != 2:
        raise ValueError(f"Format de plage de lignes invalide : '{rows_spec}'")
    return int(parts[0]), int(parts[1])


def build_auto_validated_rule(matched_code: str, new_file_path: Path) -> MappingRule | None:
    """Construit une règle adaptée au nouveau fichier à partir du mapping matché.

    Recalcule les plages de lignes (matrix/multi_table) contre la VRAIE taille
    du nouveau fichier au lieu de copier aveuglément celles du fichier
    d'origine — sinon des lignes de produits en plus par rapport au fichier
    d'origine seraient silencieusement ignorées (hors plage copiée).
    - matrix : une seule zone par feuille → borne de fin étendue à la taille
      réelle de la feuille (le row_filter existant élimine déjà les lignes
      vides au-delà des vraies données).
    - multi_table : plusieurs zones empilées sur la même feuille → la borne de
      fin d'un sous-tableau est calée juste avant l'en-tête du suivant (ou la
      fin de la feuille pour le dernier), jamais devinée.
    - table : aucun recalcul nécessaire (lit déjà jusqu'à la fin de la feuille).

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
    if rule.header_detection.mode != "explicit":
        return None

    try:
        sheets = read_workbook(new_file_path)
        _, sheet = find_sheet(sheets, rule.sheet_match)
    except Exception:
        return None
    sheet_len = len(sheet)

    try:
        if rule.extraction_mode == "matrix" and rule.data_zone is not None:
            row_start, _row_end = _parse_row_range(rule.data_zone.rows)
            new_data_zone = rule.data_zone.model_copy(
                update={"rows": f"{row_start}:{sheet_len}"}
            )
            rule = rule.model_copy(update={"data_zone": new_data_zone})

        elif rule.extraction_mode == "multi_table" and rule.tables:
            new_tables = []
            for i, sub_table in enumerate(rule.tables):
                row_start, _row_end = _parse_row_range(sub_table.zone.data_rows)
                if i + 1 < len(rule.tables):
                    new_end = rule.tables[i + 1].zone.header_row - 1
                else:
                    new_end = sheet_len
                new_zone = sub_table.zone.model_copy(
                    update={"data_rows": f"{row_start}:{new_end}"}
                )
                new_tables.append(sub_table.model_copy(update={"zone": new_zone}))
            rule = rule.model_copy(update={"tables": new_tables})
        # mode table : rien à recalculer, lit déjà jusqu'à la fin de la feuille.
    except Exception as exc:
        logger.warning(
            "recalcul de plage impossible (repli vers validation humaine)",
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
