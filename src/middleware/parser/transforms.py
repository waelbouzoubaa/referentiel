from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from middleware.parser.grammar import Transform


def apply_transform(value: Any, transform: Transform) -> Any:
    """Applique une ou plusieurs transformations sur une valeur brute.

    Args:
        value: Valeur brute issue du fichier Excel.
        transform: Nom d'une transformation ou liste de noms.

    Returns:
        Valeur transformée, ou None si la valeur source est None.
    """
    if value is None:
        return None
    # Les cellules numériques entières arrivent comme float (ex: 560077.0).
    # On les convertit en int pour que str() donne "560077" et non "560077.0".
    if isinstance(value, float) and value == int(value):
        value = int(value)
    transforms = [transform] if isinstance(transform, str) else (transform or [])
    result: Any = value
    for t in transforms:
        result = _apply_single(result, t)
    return result


def _apply_single(value: Any, transform: str) -> Any:
    """Applique une transformation unique."""
    # Les valeurs numériques ou dates d'openpyxl passent directement
    if transform == "parse_date_iso":
        return _parse_date_iso(value)
    if transform == "parse_date_fr":
        return _parse_date_fr(value)

    s = str(value).strip() if value is not None else ""

    match transform:
        case "strip":
            return s
        case "strip_upper":
            return s.upper()
        case "strip_lower":
            return s.lower()
        case "to_uppercase":
            return s.upper()
        case "to_lowercase":
            return s.lower()
        case "parse_decimal_fr":
            return _parse_decimal_fr(s)
        case "parse_decimal_us":
            return _parse_decimal_us(s)
        case "parse_duration_fr":
            return _parse_duration_fr(s)
        case "extract_integer":
            matches = re.findall(r"\d+", s)
            return matches[-1] if matches else None
        case _:
            if transform.startswith("regex_extract:"):
                pattern = transform.split(":", 1)[1].strip("'\"")
                return _regex_extract(s, pattern)
            if transform.startswith("prepend:"):
                prefix = transform.split(":", 1)[1].strip("'\"")
                return f"{prefix}{s}"
            if transform.startswith("default:"):
                default = transform.split(":", 1)[1].strip("'\"")
                return s or default
            raise ValueError(f"Transformation inconnue : '{transform}'")


# ── Parseurs de types ─────────────────────────────────────────────────────────

def _parse_decimal_fr(s: str) -> Decimal:
    """Parse un décimal au format français : virgule comme séparateur décimal.

    Exemples : "125,50" → 125.50 | "1 250,00" → 1250.00
    """
    cleaned = re.sub(r"[\s\xa0]", "", s)  # retire espaces et espaces insécables
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Impossible de parser le décimal français : '{s}'") from exc


def _parse_decimal_us(s: str) -> Decimal:
    """Parse un décimal au format US : point comme séparateur décimal."""
    cleaned = re.sub(r"[^\d.\-]", "", s)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Impossible de parser le décimal US : '{s}'") from exc


def _parse_date_iso(value: Any) -> date:
    """Parse une date ISO ou un objet datetime/date d'openpyxl."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Impossible de parser la date ISO : '{value}'")


def _parse_date_fr(value: Any) -> date:
    """Parse une date au format français DD/MM/YYYY."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Impossible de parser la date française : '{value}'")


def _parse_duration_fr(s: str) -> Decimal:
    """Parse une durée au format français : '4,33H' → 4.33."""
    cleaned = re.sub(r"[Hh]", "", s).strip()
    return _parse_decimal_fr(cleaned)


def _regex_extract(s: str, pattern: str) -> str | None:
    """Extrait le premier groupe capturant d'un pattern regex."""
    m = re.search(pattern, s)
    if m:
        return m.group(1) if m.lastindex else m.group(0)
    return None


# ── Utilitaires ───────────────────────────────────────────────────────────────

def col_letter_to_idx(col: str) -> int:
    """Convertit une lettre de colonne Excel en index 0-based.

    Exemples : A → 0 | B → 1 | Z → 25 | AA → 26
    """
    result = 0
    for char in col.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def cell_ref_to_row_col(cell_ref: str) -> tuple[int, int]:
    """Convertit une référence de cellule en (row_0based, col_0based).

    Exemple : 'C4' → (3, 2)
    """
    m = re.match(r"([A-Za-z]+)(\d+)", cell_ref)
    if not m:
        raise ValueError(f"Référence de cellule invalide : '{cell_ref}'")
    col_str, row_str = m.group(1), m.group(2)
    return int(row_str) - 1, col_letter_to_idx(col_str)
