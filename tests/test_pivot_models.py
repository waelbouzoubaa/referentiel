from __future__ import annotations

from decimal import Decimal

import pytest

from middleware.parser.pivot import (
    AttributePivot,
    CommercialRulePivot,
    FileMetadataPivot,
    PricePivot,
    ProductPivot,
    VariantPivot,
)


def test_price_pivot_valide() -> None:
    p = PricePivot(price_type="installer", amount=Decimal("125.50"), currency="EUR")
    assert p.amount == Decimal("125.50")
    assert p.currency == "EUR"


def test_price_pivot_montant_negatif() -> None:
    with pytest.raises(ValueError, match="négatif"):
        PricePivot(price_type="public", amount=Decimal("-1.00"))


def test_price_pivot_validite_incoherente() -> None:
    from datetime import date
    with pytest.raises(ValueError, match="valid_from"):
        PricePivot(
            price_type="public",
            amount=Decimal("10.00"),
            valid_from=date(2026, 12, 31),
            valid_to=date(2026, 1, 1),
        )


def test_product_pivot_atlantic() -> None:
    p = ProductPivot(
        supplier_code="atlantic_scga_chauffage",
        supplier_product_code="341073",
        designation="ARTICLE ATLANTIC TEST1",
        product_kind="physical",
        family="Chauffage électrique",
        prices=[
            PricePivot(price_type="public", amount=Decimal("146.00")),
            PricePivot(price_type="installer", amount=Decimal("145.00")),
        ],
        attributes=[AttributePivot(key="quantity_pack", value="1", data_type="integer")],
    )
    assert len(p.prices) == 2
    assert len(p.all_prices()) == 2


def test_product_pivot_avec_variants_airisol() -> None:
    p = ProductPivot(
        supplier_code="airisol",
        supplier_product_code="AIR-TEST1",
        designation="ARTICLE AIRISOL TEST1",
        product_kind="physical",
        variants=[
            VariantPivot(
                variant_dimension="couleur",
                variant_value="ALU",
                variant_code="alu",
                prices=[PricePivot(price_type="list", amount=Decimal("5.01"))],
            ),
            VariantPivot(
                variant_dimension="couleur",
                variant_value="BLANC",
                variant_code="blanc",
                prices=[PricePivot(price_type="list", amount=Decimal("7.11"))],
            ),
        ],
    )
    assert len(p.all_prices()) == 2


def test_product_pivot_designation_vide() -> None:
    with pytest.raises(ValueError):
        ProductPivot(
            supplier_code="test",
            supplier_product_code="CODE",
            designation="   ",
        )


def test_product_pivot_kind_invalide() -> None:
    with pytest.raises(ValueError, match="product_kind invalide"):
        ProductPivot(
            supplier_code="test",
            supplier_product_code="CODE",
            designation="Test",
            product_kind="inconnu",
        )


def test_attribute_pivot_type_invalide() -> None:
    with pytest.raises(ValueError, match="data_type invalide"):
        AttributePivot(key="test", value="50", data_type="mauvais_type")


def test_product_pivot_service_agenor() -> None:
    p = ProductPivot(
        supplier_code="agenor",
        supplier_product_code="AGEN-EBV-1-2-bungalows-1x_semaine",
        designation="Entretien base vie 1 à 2 bungalows — 1x_semaine",
        product_kind="service",
        family="Entretien",
        prices=[PricePivot(price_type="forfait", amount=Decimal("106.08"))],
    )
    assert p.product_kind == "service"
