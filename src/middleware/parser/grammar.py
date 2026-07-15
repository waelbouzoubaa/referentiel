from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Transformations ───────────────────────────────────────────────────────────

Transform = Union[str, list[str], None]

TRANSFORMS_VALIDES = {
    "strip",
    "strip_upper",
    "strip_lower",
    "to_uppercase",
    "to_lowercase",
    "parse_decimal_fr",
    "parse_decimal_us",
    "parse_date_fr",
    "parse_date_iso",
    "parse_duration_fr",
}


def validate_transforms(v: Transform) -> Transform:
    """Valide que les noms de transformations sont connus."""
    transforms = [v] if isinstance(v, str) else (v or [])
    for t in transforms:
        base = t.split(":")[0].split("(")[0]
        if base not in TRANSFORMS_VALIDES:
            raise ValueError(f"Transformation inconnue : '{t}'.")
    return v


# ── Mapping d'une colonne ─────────────────────────────────────────────────────

class ColumnMapping(BaseModel):
    """Décrit comment extraire un champ depuis le fichier Excel."""

    source_col: str | None = None
    constant: str | None = None
    derived_from: str | None = None
    transform: Transform = None
    required: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> ColumnMapping:
        sources = [self.source_col, self.constant, self.derived_from]
        nb = sum(1 for s in sources if s is not None)
        if nb != 1:
            raise ValueError(
                "Exactement une source requise parmi : source_col, constant, derived_from."
            )
        return self


# ── Mapping d'un prix ─────────────────────────────────────────────────────────

class PriceMapping(BaseModel):
    """Décrit comment extraire un prix depuis le fichier Excel."""

    type: str
    source_col: str
    transform: Transform = "parse_decimal_fr"
    currency: str = "EUR"


# ── Mapping d'un attribut ─────────────────────────────────────────────────────

class AttributeMapping(BaseModel):
    """Décrit comment extraire un attribut technique."""

    key: str
    source_col: str
    data_type: str = "string"
    unit: str | None = None
    transform: Transform = None
    enum_values: list[str] | None = None


# ── Filtres de lignes ─────────────────────────────────────────────────────────

class RowFilter(BaseModel):
    """Filtres pour exclure les lignes non-données (séparateurs, totaux, etc.)."""

    must_have_value_in: list[str] = Field(default_factory=list)
    must_have_value_in_any: list[str] = Field(default_factory=list)
    exclude_if_starts_with: list[str] = Field(default_factory=list)


# ── Métadonnées du fichier ────────────────────────────────────────────────────

class CellExtraction(BaseModel):
    """Extraction depuis une cellule fixe."""

    cell: str | None = None
    regex: str | None = None
    in_cell: str | None = None
    transform: Transform = None
    constant: str | None = None
    captures: dict[str, int] | None = None

    @model_validator(mode="after")
    def has_source(self) -> CellExtraction:
        if not any([self.cell, self.regex, self.constant]):
            raise ValueError("Au moins une source requise : cell, regex ou constant.")
        return self


class FileMetadataMapping(BaseModel):
    """Décrit comment extraire les métadonnées du cartouche fournisseur."""

    validity_start: CellExtraction | None = None
    validity_end: CellExtraction | None = None
    contract_reference: CellExtraction | None = None
    geographic_scope: CellExtraction | None = None
    organizational_scope: CellExtraction | None = None
    client_article_code: CellExtraction | None = None
    validity_period: CellExtraction | None = None
    ramery_generic_code: CellExtraction | None = None
    siren_fournisseur: CellExtraction | None = None


# ── Config export Gery ────────────────────────────────────────────────────────

class PriceExportMapping(BaseModel):
    """Quel prix pivot va dans Direct Unit Cost Gery."""

    direct_unit_cost: str = "installer"


class GeryExportConfig(BaseModel):
    """Configuration de la génération du fichier d'import Gery."""

    enabled: bool
    blocked_reason: str | None = None
    flatten_strategy: Literal["cartesian", "best_price_only", "skip_for_review"] = "cartesian"
    derived_code_template: str | None = None
    description_template: str | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    price_export_mapping: PriceExportMapping = Field(default_factory=PriceExportMapping)

    @model_validator(mode="after")
    def blocked_reason_required(self) -> GeryExportConfig:
        if not self.enabled and not self.blocked_reason:
            raise ValueError("blocked_reason est obligatoire quand gery_export.enabled est False.")
        return self


# ── Détection de l'en-tête ────────────────────────────────────────────────────

class HeaderDetection(BaseModel):
    """Paramètres de détection de la ligne d'en-tête."""

    mode: Literal["explicit", "auto"] = "explicit"
    row: int | None = None
    hint: dict[str, Any] | None = None

    @model_validator(mode="after")
    def row_required_if_explicit(self) -> HeaderDetection:
        if self.mode == "explicit" and self.row is None:
            raise ValueError("header_detection.row est obligatoire en mode explicit.")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# MODE TABLE (Atlantic)
# ─────────────────────────────────────────────────────────────────────────────

class TableMappingConfig(BaseModel):
    """Configuration spécifique au mode d'extraction table (Atlantic)."""

    columns: dict[str, ColumnMapping]
    prices: list[PriceMapping] = Field(default_factory=list)
    attributes: list[AttributeMapping] = Field(default_factory=list)
    row_filter: RowFilter = Field(default_factory=RowFilter)


# ─────────────────────────────────────────────────────────────────────────────
# MODE MATRIX (Airisol)
# ─────────────────────────────────────────────────────────────────────────────

class ColumnGroup(BaseModel):
    """Groupe de colonnes de la matrice (tier × variante)."""

    columns: list[str]
    tier_label: str
    variants: list[str]


class TierAxis(BaseModel):
    """Axe des paliers de quantité (ligne d'en-tête supérieure)."""

    header_row: int
    type: Literal["quantity_range"] = "quantity_range"
    fallback_unit: str = "m²"
    detect_per_block: bool = False


class VariantAxis(BaseModel):
    """Axe des variantes (ligne d'en-tête inférieure)."""

    header_row: int
    dimension_name: str


class PriceMatrixConfig(BaseModel):
    """Configuration de la matrice de prix (Airisol)."""

    tier_axis: TierAxis
    variant_axis: VariantAxis
    column_groups: list[ColumnGroup]
    price_type: str = "list"
    currency: str = "EUR"
    transform: Transform = "parse_decimal_fr"


class DataZone(BaseModel):
    """Zone de données dans la feuille (pour le mode matrix)."""

    rows: str
    product_columns: str
    price_matrix_columns: str


class CommercialRuleMapping(BaseModel):
    """Extraction d'une règle commerciale depuis le fichier."""

    source_col: str
    rule_type: str
    threshold_unit: str | None = None
    parse_pattern: str | None = None
    applies_to: Literal["product", "file"] = "product"


class MatrixMappingConfig(BaseModel):
    """Configuration spécifique au mode d'extraction matrix (Airisol)."""

    data_zone: DataZone
    product_columns: dict[str, ColumnMapping]
    attributes: list[AttributeMapping] = Field(default_factory=list)
    price_matrix: PriceMatrixConfig
    commercial_rules: list[CommercialRuleMapping] = Field(default_factory=list)
    row_filter: RowFilter = Field(default_factory=RowFilter)


# ─────────────────────────────────────────────────────────────────────────────
# MODE MULTI_TABLE (Agenor)
# ─────────────────────────────────────────────────────────────────────────────

class TableZone(BaseModel):
    """Zone d'un tableau dans la feuille (header + data rows + colonnes)."""

    header_row: int
    data_rows: str
    cols: str


class ColDimension(BaseModel):
    """Dimension colonne d'un tableau 2D (fréquence pour Agenor)."""

    columns: list[str]
    key: str
    value: str
    price_col: str
    max_time_col: str | None = None


class ProductTemplate(BaseModel):
    """Template pour générer la désignation et le code d'un produit de service."""

    designation_template: str
    supplier_product_code_template: str
    family: str | None = None
    subfamily: str | None = None


class SubTable(BaseModel):
    """Un tableau individuel dans un fichier multi-tableaux (Agenor)."""

    name: str
    description: str = ""
    zone: TableZone
    layout: str
    row_dimension: ColumnMapping | None = None
    col_dimensions: list[ColDimension] = Field(default_factory=list)
    product_template: ProductTemplate | None = None
    prices: list[PriceMapping] = Field(default_factory=list)
    attributes: list[AttributeMapping] = Field(default_factory=list)


class MultiTableMappingConfig(BaseModel):
    """Configuration spécifique au mode d'extraction multi_table (Agenor)."""

    tables: list[SubTable]


# ─────────────────────────────────────────────────────────────────────────────
# RÈGLE DE MAPPING PRINCIPALE
# ─────────────────────────────────────────────────────────────────────────────

class MappingRule(BaseModel):
    """Document YAML de mapping complet — source de vérité du moteur d'extraction."""

    supplier_code: str
    mapping_version: int = Field(ge=1)
    description: str = ""
    upload_mode: Literal["full", "incremental"] = "incremental"

    # Nom(s) du dossier SharePoint surveillé par le watcher pour ce fournisseur.
    # Si absent, le watcher utilise supplier_code comme nom de dossier.
    sharepoint_folder: str | list[str] | None = None

    # Mots-clés à chercher dans le nom du fichier pour activer ce YAML.
    # Vide = s'applique à tous les fichiers du dossier.
    # Non-vide = s'applique uniquement si le nom de fichier contient au moins un mot-clé.
    filename_keywords: list[str] = Field(default_factory=list)

    sheet_match: str | dict[str, str] = "auto"
    header_detection: HeaderDetection = Field(default_factory=lambda: HeaderDetection(mode="explicit", row=1))
    data_starts_row: int = Field(ge=1)

    extraction_mode: Literal["table", "matrix", "multi_table"]
    product_kind: Literal["physical", "service"] = "physical"

    file_metadata: FileMetadataMapping = Field(default_factory=FileMetadataMapping)
    gery_export: GeryExportConfig

    # Sections spécifiques au mode (une seule active à la fois)
    columns: dict[str, ColumnMapping] | None = None
    prices: list[PriceMapping] | None = None
    attributes: list[AttributeMapping] | None = None
    row_filter: RowFilter | None = None
    data_zone: DataZone | None = None
    product_columns: dict[str, ColumnMapping] | None = None
    price_matrix: PriceMatrixConfig | None = None
    commercial_rules: list[CommercialRuleMapping] | None = None
    tables: list[SubTable] | None = None

    @model_validator(mode="after")
    def mode_sections_coherent(self) -> MappingRule:
        """Vérifie que les sections requises pour le mode sont présentes."""
        if self.extraction_mode == "table" and not self.columns:
            raise ValueError("columns est obligatoire pour extraction_mode=table.")
        if self.extraction_mode == "matrix":
            if not self.data_zone:
                raise ValueError("data_zone est obligatoire pour extraction_mode=matrix.")
            if not self.price_matrix:
                raise ValueError("price_matrix est obligatoire pour extraction_mode=matrix.")
        if self.extraction_mode == "multi_table" and not self.tables:
            raise ValueError("tables est obligatoire pour extraction_mode=multi_table.")
        return self

    def resolved_sharepoint_folders(self) -> list[str]:
        """Liste des noms de dossiers SharePoint surveillés pour ce fournisseur.

        Retombe sur `supplier_code` si `sharepoint_folder` n'est pas renseigné.
        """
        if self.sharepoint_folder is None:
            return [self.supplier_code]
        if isinstance(self.sharepoint_folder, str):
            return [self.sharepoint_folder]
        return list(self.sharepoint_folder)

    def as_table_config(self) -> TableMappingConfig:
        """Retourne la config table (valide uniquement si extraction_mode=table)."""
        assert self.extraction_mode == "table"
        return TableMappingConfig(
            columns=self.columns or {},
            prices=self.prices or [],
            attributes=self.attributes or [],
            row_filter=self.row_filter or RowFilter(),
        )

    def as_matrix_config(self) -> MatrixMappingConfig:
        """Retourne la config matrix (valide uniquement si extraction_mode=matrix)."""
        assert self.extraction_mode == "matrix"
        return MatrixMappingConfig(
            data_zone=self.data_zone,  # type: ignore[arg-type]
            product_columns=self.product_columns or {},
            attributes=self.attributes or [],
            price_matrix=self.price_matrix,  # type: ignore[arg-type]
            commercial_rules=self.commercial_rules or [],
            row_filter=self.row_filter or RowFilter(),
        )

    def as_multi_table_config(self) -> MultiTableMappingConfig:
        """Retourne la config multi_table (valide uniquement si extraction_mode=multi_table)."""
        assert self.extraction_mode == "multi_table"
        return MultiTableMappingConfig(tables=self.tables or [])
