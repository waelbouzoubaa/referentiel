from __future__ import annotations

import pytest

from middleware.parser.grammar import (
    ColumnMapping,
    GeryExportConfig,
    HeaderDetection,
    MappingRule,
    PriceExportMapping,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _gery_enabled() -> dict:
    return {
        "enabled": True,
        "flatten_strategy": "cartesian",
        "defaults": {"item_purchase_type": "Catalogue"},
        "price_export_mapping": {"direct_unit_cost": "installer"},
    }


def _gery_disabled() -> dict:
    return {"enabled": False, "blocked_reason": "Test"}


def _atlantic_base() -> dict:
    return {
        "supplier_code": "atlantic_scga_chauffage",
        "mapping_version": 1,
        "sheet_match": "Atlantic 2026",
        "header_detection": {"mode": "explicit", "row": 9},
        "data_starts_row": 10,
        "extraction_mode": "table",
        "columns": {
            "supplier_product_code": {"source_col": "B", "required": True},
            "designation": {"source_col": "C", "required": True},
            "family": {"constant": "Chauffage électrique"},
        },
        "prices": [
            {"type": "installer", "source_col": "F"},
        ],
        "gery_export": _gery_enabled(),
    }


# ── Tests ColumnMapping ────────────────────────────────────────────────────────

def test_column_mapping_source_col() -> None:
    m = ColumnMapping(source_col="B", required=True)
    assert m.source_col == "B"


def test_column_mapping_constant() -> None:
    m = ColumnMapping(constant="Chauffage électrique")
    assert m.constant == "Chauffage électrique"


def test_column_mapping_deux_sources() -> None:
    with pytest.raises(ValueError, match="Exactement une source"):
        ColumnMapping(source_col="B", constant="test")


def test_column_mapping_aucune_source() -> None:
    with pytest.raises(ValueError, match="Exactement une source"):
        ColumnMapping()


# ── Tests GeryExportConfig ────────────────────────────────────────────────────

def test_gery_export_disabled_sans_raison() -> None:
    with pytest.raises(ValueError, match="blocked_reason"):
        GeryExportConfig(enabled=False)


def test_gery_export_disabled_avec_raison() -> None:
    g = GeryExportConfig(enabled=False, blocked_reason="Modélisation à valider")
    assert not g.enabled


# ── Tests HeaderDetection ─────────────────────────────────────────────────────

def test_header_explicit_sans_row() -> None:
    with pytest.raises(ValueError, match="row est obligatoire"):
        HeaderDetection(mode="explicit")


def test_header_explicit_avec_row() -> None:
    h = HeaderDetection(mode="explicit", row=9)
    assert h.row == 9


# ── Tests MappingRule mode table (Atlantic) ───────────────────────────────────

def test_mapping_rule_atlantic_valide() -> None:
    rule = MappingRule.model_validate(_atlantic_base())
    assert rule.supplier_code == "atlantic_scga_chauffage"
    assert rule.extraction_mode == "table"
    assert rule.gery_export.enabled is True


def test_mapping_rule_table_sans_columns() -> None:
    data = _atlantic_base()
    del data["columns"]
    with pytest.raises(ValueError, match="columns est obligatoire"):
        MappingRule.model_validate(data)


def test_as_table_config() -> None:
    rule = MappingRule.model_validate(_atlantic_base())
    config = rule.as_table_config()
    assert "supplier_product_code" in config.columns
    assert "family" in config.columns


# ── Tests MappingRule mode matrix (Airisol) ───────────────────────────────────

def _airisol_base() -> dict:
    return {
        "supplier_code": "airisol",
        "mapping_version": 1,
        "sheet_match": "Table 1",
        "header_detection": {"mode": "explicit", "row": 9},
        "data_starts_row": 10,
        "extraction_mode": "matrix",
        "data_zone": {
            "rows": "10:31",
            "product_columns": "A:F",
            "price_matrix_columns": "G:L",
        },
        "product_columns": {
            "designation": {"source_col": "C", "required": True},
            "supplier_product_code": {"source_col": "C", "required": True},
        },
        "price_matrix": {
            "tier_axis": {"header_row": 8, "type": "quantity_range"},
            "variant_axis": {"header_row": 9, "dimension_name": "couleur"},
            "column_groups": [
                {"columns": ["G", "H"], "tier_label": "0-500m²", "variants": ["ALU", "BLANC"]},
            ],
        },
        "gery_export": _gery_enabled(),
    }


def test_mapping_rule_airisol_valide() -> None:
    rule = MappingRule.model_validate(_airisol_base())
    assert rule.extraction_mode == "matrix"
    config = rule.as_matrix_config()
    assert len(config.price_matrix.column_groups) == 1


def test_mapping_rule_matrix_sans_price_matrix() -> None:
    data = _airisol_base()
    del data["price_matrix"]
    with pytest.raises(ValueError, match="price_matrix est obligatoire"):
        MappingRule.model_validate(data)


# ── Tests MappingRule mode multi_table (Agenor) ───────────────────────────────

def _agenor_base() -> dict:
    return {
        "supplier_code": "agenor",
        "mapping_version": 1,
        "sheet_match": "Agenor 2026",
        "header_detection": {"mode": "explicit", "row": 8},
        "data_starts_row": 9,
        "extraction_mode": "multi_table",
        "product_kind": "service",
        "tables": [
            {
                "name": "entretien_bases_vie",
                "zone": {"header_row": 8, "data_rows": "9:18", "cols": "A:G"},
                "layout": "matrix_2D",
                "product_template": {
                    "designation_template": "Entretien {taille}",
                    "supplier_product_code_template": "AGEN-{taille}",
                },
                "prices": [{"type": "forfait", "source_col": "B"}],
            }
        ],
        "gery_export": _gery_disabled(),
    }


def test_mapping_rule_agenor_valide() -> None:
    rule = MappingRule.model_validate(_agenor_base())
    assert rule.extraction_mode == "multi_table"
    assert rule.product_kind == "service"
    assert not rule.gery_export.enabled


def test_mapping_rule_multi_table_sans_tables() -> None:
    data = _agenor_base()
    del data["tables"]
    with pytest.raises(ValueError, match="tables est obligatoire"):
        MappingRule.model_validate(data)
