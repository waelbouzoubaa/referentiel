from __future__ import annotations


class MiddlewareError(Exception):
    """Erreur de base du middleware."""

    def __init__(self, message: str, *, supplier_code: str | None = None) -> None:
        super().__init__(message)
        self.supplier_code = supplier_code


class MappingValidationError(MiddlewareError):
    """Le fichier YAML de mapping ne passe pas la validation Pydantic."""

    def __init__(
        self,
        message: str,
        *,
        supplier_code: str | None = None,
        yaml_field: str | None = None,
    ) -> None:
        super().__init__(message, supplier_code=supplier_code)
        self.yaml_field = yaml_field


class ParsingError(MiddlewareError):
    """Erreur lors de la lecture ou du parsing d'un fichier Excel."""

    def __init__(
        self,
        message: str,
        *,
        supplier_code: str | None = None,
        filename: str | None = None,
        row_number: int | None = None,
    ) -> None:
        super().__init__(message, supplier_code=supplier_code)
        self.filename = filename
        self.row_number = row_number


class ExportGenerationError(MiddlewareError):
    """Erreur lors de la génération d'un fichier d'export Gery."""

    def __init__(
        self,
        message: str,
        *,
        supplier_code: str | None = None,
        export_kind: str | None = None,
    ) -> None:
        super().__init__(message, supplier_code=supplier_code)
        self.export_kind = export_kind


class DeltaComputationError(MiddlewareError):
    """Erreur lors du calcul du delta entre deux versions de catalogue."""


class StorageError(MiddlewareError):
    """Erreur lors d'une opération sur le stockage objet (MinIO/GCS)."""

    def __init__(
        self,
        message: str,
        *,
        supplier_code: str | None = None,
        path: str | None = None,
    ) -> None:
        super().__init__(message, supplier_code=supplier_code)
        self.path = path


class SupplierNotFoundError(MiddlewareError):
    """Le fournisseur demandé n'existe pas en base."""
