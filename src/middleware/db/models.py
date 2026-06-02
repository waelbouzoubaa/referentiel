from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMPTZ
from sqlalchemy.orm import Mapped, mapped_column, relationship

from middleware.db.base import Base


# ── Fournisseurs ──────────────────────────────────────────────────────────────

class Supplier(Base):
    """Référentiel des fournisseurs connus du middleware."""

    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("code", name="uq_suppliers_code"),
        UniqueConstraint("sharepoint_folder", name="uq_suppliers_sharepoint_folder"),
        CheckConstraint("upload_mode IN ('full', 'incremental')", name="chk_suppliers_upload_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sage_supplier_code: Mapped[str | None] = mapped_column(String)
    sharepoint_folder: Mapped[str] = mapped_column(String, nullable=False)
    upload_mode: Mapped[str] = mapped_column(String, nullable=False, default="incremental")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    mapping_rules: Mapped[list[MappingRule]] = relationship(back_populates="supplier")
    files: Mapped[list[SupplierFile]] = relationship(back_populates="supplier")
    products: Mapped[list[Product]] = relationship(back_populates="supplier")


# ── Règles de mapping ─────────────────────────────────────────────────────────

class MappingRule(Base):
    """YAML de mapping versionné, 1 seule version active par fournisseur."""

    __tablename__ = "mapping_rules"
    __table_args__ = (
        UniqueConstraint("supplier_id", "version", name="uq_mapping_rules_supplier_version"),
        UniqueConstraint("yaml_hash", name="uq_mapping_rules_yaml_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    yaml_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validated_by: Mapped[str | None] = mapped_column(String)
    validated_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="mapping_rules")
    files: Mapped[list[SupplierFile]] = relationship(back_populates="mapping_rule")


# ── Fichiers reçus ────────────────────────────────────────────────────────────

class SupplierFile(Base):
    """Fichier Excel reçu depuis SharePoint, archivé et tracé."""

    __tablename__ = "supplier_files"
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_supplier_files_hash"),
        CheckConstraint(
            "status IN ('received','processing','processed','failed','skipped')",
            name="chk_supplier_files_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String, nullable=False)
    sharepoint_item_id: Mapped[str] = mapped_column(String, nullable=False)
    sharepoint_etag: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gcs_path: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)
    processing_started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    processing_ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    status: Mapped[str] = mapped_column(String, nullable=False, default="received")
    error_message: Mapped[str | None] = mapped_column(Text)
    validity_start: Mapped[date | None] = mapped_column(Date)
    validity_end: Mapped[date | None] = mapped_column(Date)
    contract_reference: Mapped[str | None] = mapped_column(String)
    geographic_scope: Mapped[str | None] = mapped_column(String)
    organizational_scope: Mapped[str | None] = mapped_column(String)
    mapping_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mapping_rules.id", ondelete="RESTRICT")
    )
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    supplier: Mapped[Supplier] = relationship(back_populates="files")
    mapping_rule: Mapped[MappingRule | None] = relationship(back_populates="files")
    products_first_seen: Mapped[list[Product]] = relationship(
        foreign_keys="Product.first_seen_in_file_id", back_populates="first_seen_file"
    )
    processing_errors: Mapped[list[ProcessingError]] = relationship(back_populates="supplier_file")


# ── Produits (pivot principal) ────────────────────────────────────────────────

class Product(Base):
    """Entité pivot principale : article physique ou prestation de service."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("supplier_id", "supplier_product_code", name="uq_products_supplier_code"),
        CheckConstraint("product_kind IN ('physical','service')", name="chk_products_kind"),
        CheckConstraint("status IN ('active','inactive','deleted')", name="chk_products_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_product_code: Mapped[str] = mapped_column(String, nullable=False)
    generic_code: Mapped[str | None] = mapped_column(String)
    designation: Mapped[str] = mapped_column(String, nullable=False)
    family: Mapped[str | None] = mapped_column(String)
    subfamily: Mapped[str | None] = mapped_column(String)
    product_kind: Mapped[str] = mapped_column(String, nullable=False, default="physical")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    first_seen_in_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="SET NULL")
    )
    last_seen_in_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="SET NULL")
    )
    business_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="products")
    first_seen_file: Mapped[SupplierFile | None] = relationship(
        foreign_keys=[first_seen_in_file_id], back_populates="products_first_seen"
    )
    variants: Mapped[list[ProductVariant]] = relationship(back_populates="product")
    attributes: Mapped[list[ProductAttribute]] = relationship(back_populates="product")
    prices: Mapped[list[Price]] = relationship(back_populates="product")
    commercial_rules: Mapped[list[CommercialRule]] = relationship(back_populates="product")
    history: Mapped[list[ProductHistory]] = relationship(back_populates="product")


# ── Variantes ─────────────────────────────────────────────────────────────────

class ProductVariant(Base):
    """Déclinaisons d'un produit (couleur ALU/BLANC pour Airisol, etc.)."""

    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "variant_dimension", "variant_code",
            name="uq_product_variants_product_dim_val",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    variant_dimension: Mapped[str] = mapped_column(String, nullable=False)
    variant_value: Mapped[str] = mapped_column(String, nullable=False)
    variant_code: Mapped[str] = mapped_column(String, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    product: Mapped[Product] = relationship(back_populates="variants")
    prices: Mapped[list[Price]] = relationship(back_populates="variant")


# ── Attributs techniques ──────────────────────────────────────────────────────

class ProductAttribute(Base):
    """Caractéristiques techniques typées clé/valeur (épaisseur, R-value, etc.)."""

    __tablename__ = "product_attributes"
    __table_args__ = (
        UniqueConstraint("product_id", "attribute_key", name="uq_product_attributes_product_key"),
        CheckConstraint(
            "data_type IN ('string','integer','decimal','enum','duration','boolean')",
            name="chk_product_attributes_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    attribute_key: Mapped[str] = mapped_column(String, nullable=False)
    attribute_value: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False)
    unit: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    product: Mapped[Product] = relationship(back_populates="attributes")


# ── Prix ──────────────────────────────────────────────────────────────────────

class Price(Base):
    """Prix avec contexte tarifaire complet (type, palier, variante, validité)."""

    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="chk_prices_amount_positive"),
        CheckConstraint(
            "valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
            name="chk_prices_validity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE")
    )
    price_type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    tier_min_quantity: Mapped[float | None] = mapped_column(Numeric(15, 4))
    tier_max_quantity: Mapped[float | None] = mapped_column(Numeric(15, 4))
    tier_unit: Mapped[str | None] = mapped_column(String)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    product: Mapped[Product] = relationship(back_populates="prices")
    variant: Mapped[ProductVariant | None] = relationship(back_populates="prices")


# ── Règles commerciales ───────────────────────────────────────────────────────

class CommercialRule(Base):
    """Règles commerciales (franco port, conditionnement minimum, etc.)."""

    __tablename__ = "commercial_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE")
    )
    supplier_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="CASCADE")
    )
    rule_type: Mapped[str] = mapped_column(String, nullable=False)
    threshold_value: Mapped[float | None] = mapped_column(Numeric(15, 4))
    threshold_unit: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    product: Mapped[Product | None] = relationship(back_populates="commercial_rules")


# ── Historique des changements ────────────────────────────────────────────────

class ProductHistory(Base):
    """Journal des changements métier (CREATE/UPDATE/PRICE_CHANGE/DELETE/REACTIVATE)."""

    __tablename__ = "product_history"
    __table_args__ = (
        CheckConstraint(
            "change_type IN ('CREATE','UPDATE','PRICE_CHANGE','DELETE','REACTIVATE')",
            name="chk_product_history_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    field_changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="RESTRICT"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)
    exported_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    exported_in_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gery_exports.id", ondelete="SET NULL")
    )

    product: Mapped[Product] = relationship(back_populates="history")


# ── Exports Gery ──────────────────────────────────────────────────────────────

class GeryExport(Base):
    """Trace d'un fichier d'export Gery produit (NEW_ARTICLE, NEW_ART_FRNS_CREATE, etc.)."""

    __tablename__ = "gery_exports"
    __table_args__ = (
        CheckConstraint(
            "export_kind IN ('NEW_ARTICLE','NEW_ART_FRNS_CREATE','NEW_ART_FRNS_PRICE_UPDATE')",
            name="chk_gery_exports_kind",
        ),
        CheckConstraint(
            "status IN ('generated','delivered','acknowledged','failed')",
            name="chk_gery_exports_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    export_kind: Mapped[str] = mapped_column(String, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    output_path: Mapped[str] = mapped_column(String, nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="generated")
    ack_message: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[GeryExportLine]] = relationship(back_populates="export")


class GeryExportLine(Base):
    """Traçabilité ligne par ligne d'un export Gery."""

    __tablename__ = "gery_export_lines"
    __table_args__ = (
        UniqueConstraint("export_id", "line_number", name="uq_gery_export_lines_export_line"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    export_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gery_exports.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="RESTRICT")
    )
    price_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("prices.id", ondelete="RESTRICT")
    )
    derived_code: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    export: Mapped[GeryExport] = relationship(back_populates="lines")


# ── Suggestions de mapping (phase 2 — table créée maintenant) ─────────────────

class MappingSuggestion(Base):
    """Suggestions LLM de mapping pour fichiers inconnus (activé en phase 2)."""

    __tablename__ = "mapping_suggestions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','modified')",
            name="chk_mapping_suggestions_status",
        ),
        CheckConstraint(
            "confidence_avg >= 0 AND confidence_avg <= 1",
            name="chk_mapping_suggestions_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="CASCADE"), nullable=False
    )
    suggested_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_avg: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    field_confidences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    approved_yaml: Mapped[str | None] = mapped_column(Text)
    resulting_mapping_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mapping_rules.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)


# ── Erreurs de traitement ─────────────────────────────────────────────────────

class ProcessingError(Base):
    """Erreurs ligne par ligne — jamais bloquantes au niveau batch."""

    __tablename__ = "processing_errors"
    __table_args__ = (
        CheckConstraint(
            "error_type IN ('parse_error','validation_error','mapping_error','transform_error')",
            name="chk_processing_errors_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    supplier_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_files.id", ondelete="CASCADE"), nullable=False
    )
    row_number: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str] = mapped_column(String, nullable=False)
    error_field: Mapped[str | None] = mapped_column(String)
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, default=datetime.utcnow)

    supplier_file: Mapped[SupplierFile] = relationship(back_populates="processing_errors")
