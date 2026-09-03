"""Registre des empreintes structurelles des mappings approuvés.

Permet de reconnaître qu'un nouveau fichier partage EXACTEMENT la même
disposition d'en-tête (mêmes libellés, aux mêmes colonnes, à la même ligne)
qu'un fournisseur déjà validé — même si le fichier vient d'un dossier/nom
différent — pour proposer directement ce mapping comme suggestion, sans appel
IA. Comparaison stricte, aucune tolérance : normalisation uniquement sur les
espaces superflus et la casse (voir _normalize) — un libellé manquant, en
trop, ou décalé d'une seule colonne suffit à invalider la correspondance.
"""
from __future__ import annotations

import json
from pathlib import Path

from middleware.core.logging import get_logger
from middleware.parser.excel_reader import find_sheet, read_workbook
from middleware.parser.grammar import MappingRule
from middleware.parser.transforms import idx_to_col_letter

logger = get_logger(__name__)

INDEX_FILE = Path("config/structure_index.json")

# Nombre de lignes scannées par feuille pour chercher une ligne d'en-tête candidate
# dans un fichier inconnu — les en-têtes réels de ce projet sont toujours dans ce
# périmètre (le plus loin rencontré à ce jour est la ligne 10, cf. Atlantic).
_MAX_SCAN_ROWS = 30

# Une ligne avec trop peu de cellules non vides n'est pas fiable comme empreinte
# (risque de faux positif sur une ligne quasi vide) — ni enregistrée, ni comparée.
_MIN_SIGNATURE_CELLS = 3

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


def update_fingerprint(supplier_code: str, file_path: Path, rule: MappingRule) -> None:
    """Recalcule et enregistre l'empreinte structurelle d'un fournisseur tout juste approuvé.

    Best-effort : une erreur ici est journalisée mais ne remonte jamais — ce n'est
    qu'un raccourci pour les prochains fichiers, pas une étape critique.
    """
    try:
        signature = extract_header_signature(file_path, rule)
        if signature is None:
            return
        index = _load_index()
        index[supplier_code] = signature
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
    index = _load_index()
    known = {code: sig for code, sig in index.items() if sig}
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


def _load_index() -> dict[str, HeaderSignature]:
    if not INDEX_FILE.exists():
        return {}
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_index(index: dict[str, HeaderSignature]) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
