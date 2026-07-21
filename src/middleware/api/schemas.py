from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


# ─── Fournisseurs ─────────────────────────────────────────────────────────────

class SupplierOut(BaseModel):
    code: str
    name: str
    active: bool
    upload_mode: str
    sharepoint_folder: str
    notes: str | None = None


# ─── Traitement de fichier ────────────────────────────────────────────────────

class ProcessFileRequest(BaseModel):
    supplier_code: str
    file_path: str
    dry_run: bool = False


class PriceOut(BaseModel):
    price_type: str
    amount: Decimal
    currency: str = "EUR"
    tier_min_quantity: Decimal | None = None
    tier_max_quantity: Decimal | None = None
    tier_unit: str | None = None


class AttributeOut(BaseModel):
    key: str
    value: str
    data_type: str
    unit: str | None = None


class VariantOut(BaseModel):
    variant_dimension: str
    variant_value: str
    variant_code: str
    prices: list[PriceOut] = []


class ProductOut(BaseModel):
    supplier_product_code: str
    designation: str
    family: str | None = None
    subfamily: str | None = None
    product_kind: str
    prices: list[PriceOut] = []
    variants: list[VariantOut] = []
    attributes: list[AttributeOut] = []
    source_row: int | None = None


class DeltaSummary(BaseModel):
    creates: int
    updates: int
    price_changes: int
    deletes: int
    reactivates: int
    unchanged: int
    total_changes: int


class ProcessFileResponse(BaseModel):
    supplier_code: str
    filename: str
    products_parsed: int
    error_count: int
    delta: DeltaSummary | None = None
    dry_run: bool = False
    parsed_at: datetime


# ─── Exports Gery ─────────────────────────────────────────────────────────────

class GenerateExportsRequest(BaseModel):
    supplier_code: str
    file_path: str
    output_dir: str = "exports"
    original_filename: str | None = None
    sharepoint_item_id: str | None = None
    folder_name: str | None = None
    web_url: str | None = None


class GeneratedFileOut(BaseModel):
    kind: str
    path: str
    line_count: int
    output_hash: str


class GenerateExportsResponse(BaseModel):
    supplier_code: str
    files: list[GeneratedFileOut] = []
    generated_at: datetime
    pending_id: str
    pending_issues: list[str] = []


# ─── Audit ────────────────────────────────────────────────────────────────────

import uuid as _uuid

class AuditEntryOut(BaseModel):
    id: _uuid.UUID
    changed_at: datetime
    supplier_code: str
    supplier_product_code: str
    designation: str
    field_name: str
    source_file: str


class AuditResponse(BaseModel):
    total: int
    limit: int
    offset: int
    entries: list[AuditEntryOut]


# ─── Historique produit ───────────────────────────────────────────────────────

class ProductHistoryEntry(BaseModel):
    change_type: str
    field_changes: dict[str, Any] | None = None
    detected_at: datetime
    exported_at: datetime | None = None


class ProductHistoryResponse(BaseModel):
    supplier_product_code: str
    supplier_code: str
    history: list[ProductHistoryEntry]
