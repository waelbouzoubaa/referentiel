from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class PricePivot(BaseModel):
    """Prix avec son contexte tarifaire complet."""

    price_type: str
    amount: Decimal
    currency: str = "EUR"
    tier_min_quantity: Decimal | None = None
    tier_max_quantity: Decimal | None = None
    tier_unit: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Le montant d'un prix ne peut pas être négatif.")
        return v

    @model_validator(mode="after")
    def validity_coherent(self) -> PricePivot:
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from doit être antérieur à valid_to.")
        return self


class VariantPivot(BaseModel):
    """Déclinaison d'un produit (couleur ALU/BLANC, taille, etc.)."""

    variant_dimension: str
    variant_value: str
    variant_code: str
    display_order: int = 0
    prices: list[PricePivot] = Field(default_factory=list)


class AttributePivot(BaseModel):
    """Caractéristique technique typée clé/valeur."""

    key: str
    value: str
    data_type: str = "string"
    unit: str | None = None

    @field_validator("data_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        allowed = {"string", "integer", "decimal", "enum", "duration", "boolean"}
        if v not in allowed:
            raise ValueError(f"data_type invalide : {v}. Valeurs : {allowed}")
        return v


class CommercialRulePivot(BaseModel):
    """Règle commerciale structurée (franco port, conditionnement min, etc.)."""

    rule_type: str
    threshold_value: Decimal | None = None
    threshold_unit: str | None = None
    description: str | None = None
    raw_text: str | None = None


class FileMetadataPivot(BaseModel):
    """Métadonnées extraites du cartouche du fichier fournisseur."""

    validity_start: date | None = None
    validity_end: date | None = None
    contract_reference: str | None = None
    geographic_scope: str | None = None
    organizational_scope: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ProductPivot(BaseModel):
    """Entité pivot principale issue du parsing d'un fichier fournisseur."""

    supplier_code: str
    supplier_product_code: str
    designation: str
    product_kind: str = "physical"
    family: str | None = None
    subfamily: str | None = None
    generic_code: str | None = None
    variants: list[VariantPivot] = Field(default_factory=list)
    prices: list[PricePivot] = Field(default_factory=list)
    attributes: list[AttributePivot] = Field(default_factory=list)
    commercial_rules: list[CommercialRulePivot] = Field(default_factory=list)
    source_row: int | None = None

    @field_validator("product_kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        if v not in ("physical", "service"):
            raise ValueError(f"product_kind invalide : {v}. Valeurs : physical, service")
        return v

    @field_validator("supplier_product_code", "designation")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Le champ ne peut pas être vide.")
        return v.strip()

    def all_prices(self) -> list[PricePivot]:
        """Retourne tous les prix : directs + ceux des variantes."""
        result = list(self.prices)
        for variant in self.variants:
            result.extend(variant.prices)
        return result


class ParsingResult(BaseModel):
    """Résultat complet du parsing d'un fichier fournisseur."""

    supplier_code: str
    filename: str
    products: list[ProductPivot] = Field(default_factory=list)
    file_metadata: FileMetadataPivot = Field(default_factory=FileMetadataPivot)
    error_count: int = 0
    parsed_at: datetime = Field(default_factory=datetime.utcnow)
