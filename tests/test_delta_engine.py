from __future__ import annotations

from decimal import Decimal

import pytest

from middleware.delta.engine import ChangeType, DeltaResult, ProductDelta, compute_delta
from middleware.parser.pivot import AttributePivot, PricePivot, ProductPivot
from middleware.parser.table_extractor import compute_business_hash, compute_business_hash_no_prices


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_product(
    code: str,
    designation: str = "Article Test",
    family: str = "Famille",
    public: str = "100",
    installer: str = "90",
    supplier: str = "test_supplier",
) -> ProductPivot:
    return ProductPivot(
        supplier_code=supplier,
        supplier_product_code=code,
        designation=designation,
        family=family,
        prices=[
            PricePivot(price_type="public", amount=Decimal(public)),
            PricePivot(price_type="installer", amount=Decimal(installer)),
        ],
    )


def _hashes(products: list[ProductPivot]) -> dict[str, str]:
    return {p.supplier_product_code: compute_business_hash(p) for p in products}


def _hashes_no_prices(products: list[ProductPivot]) -> dict[str, str]:
    return {p.supplier_product_code: compute_business_hash_no_prices(p) for p in products}


# ─────────────────────────────────────────────────────────────────────────────
# Tests — CREATE
# ─────────────────────────────────────────────────────────────────────────────

def test_create_new_product() -> None:
    new = [_make_product("CODE001")]
    result = compute_delta(new, known_hashes={})
    assert len(result.creates) == 1
    assert result.creates[0].change_type == ChangeType.CREATE
    assert result.creates[0].supplier_product_code == "CODE001"
    assert result.unchanged == 0


def test_create_multiple() -> None:
    new = [_make_product(f"CODE{i:03d}") for i in range(5)]
    result = compute_delta(new, known_hashes={})
    assert len(result.creates) == 5
    assert result.total_changes == 5


# ─────────────────────────────────────────────────────────────────────────────
# Tests — UNCHANGED
# ─────────────────────────────────────────────────────────────────────────────

def test_unchanged_product() -> None:
    products = [_make_product("CODE001")]
    hashes = _hashes(products)
    result = compute_delta(products, known_hashes=hashes)
    assert result.unchanged == 1
    assert result.total_changes == 0


def test_unchanged_multiple() -> None:
    products = [_make_product(f"CODE{i:03d}") for i in range(3)]
    hashes = _hashes(products)
    result = compute_delta(products, known_hashes=hashes)
    assert result.unchanged == 3
    assert result.total_changes == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests — PRICE_CHANGE
# ─────────────────────────────────────────────────────────────────────────────

def test_price_change_detected() -> None:
    old = _make_product("CODE001", installer="90")
    new = _make_product("CODE001", installer="85")  # prix installateur baisse

    result = compute_delta(
        [new], known_hashes=_hashes([old]), known_hashes_no_prices=_hashes_no_prices([old])
    )
    assert len(result.price_changes) == 1
    assert result.price_changes[0].change_type == ChangeType.PRICE_CHANGE
    assert result.price_changes[0].supplier_product_code == "CODE001"


def test_price_change_has_field_changes() -> None:
    old = _make_product("CODE001", installer="90")
    new = _make_product("CODE001", installer="85")

    result = compute_delta(
        [new], known_hashes=_hashes([old]), known_hashes_no_prices=_hashes_no_prices([old])
    )
    delta = result.price_changes[0]
    assert "prices" in delta.field_changes


def test_price_change_has_hashes() -> None:
    old = _make_product("CODE001", installer="90")
    new = _make_product("CODE001", installer="85")

    result = compute_delta(
        [new], known_hashes=_hashes([old]), known_hashes_no_prices=_hashes_no_prices([old])
    )
    delta = result.price_changes[0]
    assert delta.previous_hash is not None
    assert delta.new_hash is not None
    assert delta.previous_hash != delta.new_hash


# ─────────────────────────────────────────────────────────────────────────────
# Tests — UPDATE
# ─────────────────────────────────────────────────────────────────────────────

def test_update_detected_on_designation_change() -> None:
    old = _make_product("CODE001", designation="Ancien libellé")
    new = _make_product("CODE001", designation="Nouveau libellé")  # prix inchangés

    result = compute_delta(
        [new], known_hashes=_hashes([old]), known_hashes_no_prices=_hashes_no_prices([old])
    )
    assert len(result.updates) == 1
    assert len(result.price_changes) == 0
    assert result.updates[0].change_type == ChangeType.UPDATE
    assert result.updates[0].supplier_product_code == "CODE001"


def test_update_takes_priority_over_price_change_when_both_change() -> None:
    old = _make_product("CODE001", designation="Ancien libellé", installer="90")
    new = _make_product("CODE001", designation="Nouveau libellé", installer="85")

    result = compute_delta(
        [new], known_hashes=_hashes([old]), known_hashes_no_prices=_hashes_no_prices([old])
    )
    assert len(result.updates) == 1
    assert len(result.price_changes) == 0


def test_change_classified_as_update_when_no_prior_hash_no_prices() -> None:
    """Produit créé avant l'ajout de business_hash_no_prices (valeur absente en base) :
    on ne peut pas savoir si seul le prix a changé → on classe prudemment en UPDATE."""
    old = _make_product("CODE001", installer="90")
    new = _make_product("CODE001", installer="85")

    result = compute_delta([new], known_hashes=_hashes([old]), known_hashes_no_prices={})
    assert len(result.updates) == 1
    assert len(result.price_changes) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests — DELETE
# ─────────────────────────────────────────────────────────────────────────────

def test_delete_absent_product() -> None:
    old_products = [_make_product("CODE001"), _make_product("CODE002")]
    new_products = [_make_product("CODE001")]  # CODE002 absent

    result = compute_delta(new_products, known_hashes=_hashes(old_products))
    assert len(result.deletes) == 1
    assert result.deletes[0].supplier_product_code == "CODE002"
    assert result.deletes[0].change_type == ChangeType.DELETE


def test_delete_all_absent() -> None:
    old_products = [_make_product("CODE001"), _make_product("CODE002")]
    result = compute_delta([], known_hashes=_hashes(old_products))
    assert len(result.deletes) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Tests — REACTIVATE
# ─────────────────────────────────────────────────────────────────────────────

def test_reactivate_deleted_product() -> None:
    old = _make_product("CODE001")
    new = _make_product("CODE001")

    # CODE001 est connu mais marqué supprimé
    result = compute_delta(
        [new],
        known_hashes=_hashes([old]),
        deleted_codes={"CODE001"},
    )
    assert len(result.reactivates) == 1
    assert result.reactivates[0].change_type == ChangeType.REACTIVATE


def test_reactivate_not_confused_with_unchanged() -> None:
    """Un produit deleted qui revient est REACTIVATE, pas UNCHANGED."""
    product = _make_product("CODE001")
    result = compute_delta(
        [product],
        known_hashes=_hashes([product]),
        deleted_codes={"CODE001"},
    )
    assert result.unchanged == 0
    assert len(result.reactivates) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Scénario mixte
# ─────────────────────────────────────────────────────────────────────────────

def test_mixed_scenario() -> None:
    """CREATE + UNCHANGED + PRICE_CHANGE + DELETE dans un seul batch."""
    # État connu en base
    existing = [
        _make_product("CODE001"),       # sera UNCHANGED
        _make_product("CODE002", installer="90"),  # prix va changer
        _make_product("CODE003"),       # va disparaître → DELETE
    ]
    known = _hashes(existing)
    known_no_prices = _hashes_no_prices(existing)

    # Nouveau snapshot
    new_snapshot = [
        _make_product("CODE001"),                     # UNCHANGED
        _make_product("CODE002", installer="80"),     # PRICE_CHANGE
        _make_product("CODE004"),                     # CREATE
        # CODE003 absent → DELETE
    ]

    result = compute_delta(new_snapshot, known_hashes=known, known_hashes_no_prices=known_no_prices)

    assert result.unchanged == 1
    assert len(result.price_changes) == 1
    assert len(result.creates) == 1
    assert len(result.deletes) == 1
    assert result.total_changes == 3


# ─────────────────────────────────────────────────────────────────────────────
# Tests — DeltaResult helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_all_deltas_order() -> None:
    """all_deltas() retourne : creates + reactivates + updates + price_changes + deletes."""
    result = DeltaResult()
    result.creates.append(ProductDelta(ChangeType.CREATE, "C1", "s"))
    result.deletes.append(ProductDelta(ChangeType.DELETE, "D1", "s"))
    result.price_changes.append(ProductDelta(ChangeType.PRICE_CHANGE, "P1", "s"))

    all_d = result.all_deltas()
    assert all_d[0].supplier_product_code == "C1"
    assert all_d[-1].supplier_product_code == "D1"
    assert len(all_d) == 3
