"""Tests — reconnaissance de structure (empreinte d'en-tête exacte, sans IA)."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

from middleware.parser.grammar import HeaderDetection, MappingRule
from middleware.structure_index import (
    extract_header_signature,
    find_matching_supplier,
    update_fingerprint,
)

_HEADERS = {"B": "Code article", "C": "Désignation", "D": "Prix"}


def _rule(row: int = 9) -> MappingRule:
    return MappingRule.model_validate({
        "supplier_code": "atlantic_scga_chauffage",
        "mapping_version": 1,
        "sheet_match": "auto",
        "header_detection": {"mode": "explicit", "row": row},
        "data_starts_row": row + 1,
        "extraction_mode": "table",
        "columns": {
            "supplier_product_code": {"source_col": "B", "required": True},
            "designation": {"source_col": "C", "required": True},
        },
        "gery_export": {"enabled": True, "flatten_strategy": "cartesian"},
    })


def _make_file(tmp_path: Path, name: str, header_row: int, headers: dict[str, str]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    for col, text in headers.items():
        ws[f"{col}{header_row}"] = text
    ws[f"B{header_row + 1}"] = "CODE001"
    ws[f"C{header_row + 1}"] = "Un produit"
    path = tmp_path / name
    wb.save(path)
    return path


@pytest.fixture(autouse=True)
def _isolated_index(tmp_path, monkeypatch):
    """Redirige le registre vers un fichier temporaire — jamais le vrai config/."""
    import middleware.structure_index as si
    monkeypatch.setattr(si, "INDEX_FILE", tmp_path / "structure_index.json")
    yield


def test_meme_structure_reconnue_malgre_nom_different(tmp_path: Path) -> None:
    rule = _rule(row=9)
    original = _make_file(tmp_path, "original.xlsx", 9, _HEADERS)
    update_fingerprint("atlantic_scga_chauffage", original, rule)

    duplicate = _make_file(tmp_path, "AUTRE_FOURNISSEUR_v2.xlsx", 9, _HEADERS)
    assert find_matching_supplier(duplicate) == "atlantic_scga_chauffage"


def test_espace_et_casse_ignores(tmp_path: Path) -> None:
    rule = _rule(row=9)
    original = _make_file(tmp_path, "original.xlsx", 9, _HEADERS)
    update_fingerprint("atlantic_scga_chauffage", original, rule)

    variant_headers = {"B": "  code   article", "C": "désignation", "D": "PRIX"}
    variant = _make_file(tmp_path, "variant.xlsx", 9, variant_headers)
    assert find_matching_supplier(variant) == "atlantic_scga_chauffage"


def test_decalage_de_colonne_ne_matche_pas(tmp_path: Path) -> None:
    rule = _rule(row=9)
    original = _make_file(tmp_path, "original.xlsx", 9, _HEADERS)
    update_fingerprint("atlantic_scga_chauffage", original, rule)

    # Même libellés, mais décalés d'une colonne vers la droite (C/D/E au lieu de B/C/D)
    shifted_headers = {"C": "Code article", "D": "Désignation", "E": "Prix"}
    shifted = _make_file(tmp_path, "shifted.xlsx", 9, shifted_headers)
    assert find_matching_supplier(shifted) is None


def test_colonne_en_plus_ne_matche_pas(tmp_path: Path) -> None:
    rule = _rule(row=9)
    original = _make_file(tmp_path, "original.xlsx", 9, _HEADERS)
    update_fingerprint("atlantic_scga_chauffage", original, rule)

    extra_headers = {**_HEADERS, "E": "Stock"}
    extra = _make_file(tmp_path, "extra.xlsx", 9, extra_headers)
    assert find_matching_supplier(extra) is None


def test_aucune_empreinte_connue(tmp_path: Path) -> None:
    fichier = _make_file(tmp_path, "seul.xlsx", 9, {"B": "Code article", "C": "Désignation"})
    assert find_matching_supplier(fichier) is None


def test_extract_header_signature_mode_auto_retourne_none(tmp_path: Path) -> None:
    rule = _rule(row=9).model_copy(update={"header_detection": HeaderDetection(mode="auto")})
    fichier = _make_file(tmp_path, "f.xlsx", 9, {"B": "Code article", "C": "Désignation"})
    assert extract_header_signature(fichier, rule) is None
