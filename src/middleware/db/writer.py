from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.core.logging import get_logger
from middleware.db.models import (
    Price,
    Product,
    ProductAttribute,
    ProductHistory,
    ProductVariant,
    Supplier,
    SupplierFile,
)
from middleware.delta.engine import DeltaResult, ProductDelta
from middleware.parser.pivot import PricePivot, ProductPivot

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Fournisseur
# ─────────────────────────────────────────────────────────────────────────────

async def get_or_create_supplier(session: AsyncSession, supplier_code: str) -> Supplier:
    """Retourne le fournisseur par code, ou en crée un minimal s'il n'existe pas encore."""
    result = await session.execute(
        select(Supplier).where(Supplier.code == supplier_code)
    )
    supplier = result.scalar_one_or_none()
    if supplier is None:
        supplier = Supplier(
            code=supplier_code,
            name=supplier_code,
            sharepoint_folder=supplier_code,
            upload_mode="full",
            active=True,
        )
        session.add(supplier)
        await session.flush()
        logger.info("fournisseur créé automatiquement", supplier_code=supplier_code)
    return supplier


# ─────────────────────────────────────────────────────────────────────────────
# Fichier fournisseur
# ─────────────────────────────────────────────────────────────────────────────

async def get_or_create_supplier_file(
    session: AsyncSession,
    supplier: Supplier,
    file_path: Path,
) -> SupplierFile:
    """Retourne le SupplierFile existant pour ce hash ou en crée un nouveau.

    La déduplication par content_hash assure l'idempotence : retraiter le même
    fichier ne crée pas de doublon en base.
    """
    content_hash = _file_hash(file_path)

    existing = await session.execute(
        select(SupplierFile).where(SupplierFile.content_hash == content_hash)
    )
    supplier_file = existing.scalar_one_or_none()
    if supplier_file is not None:
        logger.info(
            "fichier déjà connu — réutilisation du SupplierFile existant",
            filename=file_path.name,
            content_hash=content_hash,
        )
        return supplier_file

    supplier_file = SupplierFile(
        supplier_id=supplier.id,
        filename=file_path.name,
        sharepoint_item_id=file_path.name,
        content_hash=content_hash,
        size_bytes=file_path.stat().st_size,
        gcs_path=str(file_path),
        status="processing",
        processing_started_at=datetime.utcnow(),
    )
    session.add(supplier_file)
    await session.flush()
    return supplier_file


async def mark_file_processed(session: AsyncSession, supplier_file: SupplierFile) -> None:
    supplier_file.status = "processed"
    supplier_file.processing_ended_at = datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# Hashes connus pour le calcul de delta
# ─────────────────────────────────────────────────────────────────────────────

async def get_known_hashes(
    session: AsyncSession,
    supplier_id: uuid.UUID,
    upload_mode: str,
    incoming_codes: set[str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Retourne (known_hashes, deleted_codes) pour alimenter compute_delta.

    - full : tous les produits du fournisseur sont chargés — les absents du nouveau
      fichier génèrent des DELETEs.
    - incremental : seuls les produits présents dans le nouveau fichier sont chargés —
      les absents ne génèrent pas de DELETEs.
    """
    query = select(
        Product.supplier_product_code,
        Product.business_hash,
        Product.status,
    ).where(Product.supplier_id == supplier_id)

    if upload_mode == "incremental" and incoming_codes:
        query = query.where(Product.supplier_product_code.in_(incoming_codes))

    rows = (await session.execute(query)).all()

    known_hashes = {row[0]: row[1] for row in rows}
    deleted_codes = {row[0] for row in rows if row[2] in ("inactive", "deleted")}

    return known_hashes, deleted_codes


# ─────────────────────────────────────────────────────────────────────────────
# Persistance du delta
# ─────────────────────────────────────────────────────────────────────────────

async def persist_delta(
    session: AsyncSession,
    delta: DeltaResult,
    supplier_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    """Persiste tous les changements du DeltaResult en base.

    - creates / reactivates → upsert complet (produit + variantes + prix + attributs)
    - updates / price_changes → mise à jour du hash + remplacement des prix
    - deletes → passage du statut à 'inactive'
    """
    for d in delta.creates:
        await _insert_product(session, d, supplier_id, file_id, "CREATE")

    for d in delta.updates:
        await _upsert_product(session, d, supplier_id, file_id, "UPDATE")

    for d in delta.price_changes:
        await _upsert_product(session, d, supplier_id, file_id, "PRICE_CHANGE")

    for d in delta.reactivates:
        await _upsert_product(session, d, supplier_id, file_id, "REACTIVATE")

    for d in delta.deletes:
        await _soft_delete_product(session, d, supplier_id, file_id)

    logger.info(
        "delta persisté en base",
        creates=len(delta.creates),
        updates=len(delta.updates),
        price_changes=len(delta.price_changes),
        deletes=len(delta.deletes),
        reactivates=len(delta.reactivates),
        unchanged=delta.unchanged,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

async def _insert_product(
    session: AsyncSession,
    d: ProductDelta,
    supplier_id: uuid.UUID,
    file_id: uuid.UUID,
    change_type: str,
) -> None:
    p = d.new_product
    db_product = Product(
        supplier_id=supplier_id,
        supplier_product_code=p.supplier_product_code,
        designation=p.designation,
        family=p.family,
        subfamily=p.subfamily,
        product_kind=p.product_kind,
        status="active",
        business_hash=d.new_hash or "",
        first_seen_in_file_id=file_id,
        last_seen_in_file_id=file_id,
    )
    session.add(db_product)
    await session.flush()

    await _insert_variants_and_prices(session, p, db_product.id, file_id)
    await _insert_attributes(session, p, db_product.id)
    session.add(ProductHistory(
        product_id=db_product.id,
        change_type=change_type,
        source_file_id=file_id,
    ))


async def _upsert_product(
    session: AsyncSession,
    d: ProductDelta,
    supplier_id: uuid.UUID,
    file_id: uuid.UUID,
    change_type: str,
) -> None:
    result = await session.execute(
        select(Product).where(
            Product.supplier_id == supplier_id,
            Product.supplier_product_code == d.supplier_product_code,
        )
    )
    db_product = result.scalar_one_or_none()
    if db_product is None:
        await _insert_product(session, d, supplier_id, file_id, change_type)
        return

    p = d.new_product
    db_product.designation = p.designation
    db_product.family = p.family
    db_product.subfamily = p.subfamily
    db_product.business_hash = d.new_hash or ""
    db_product.status = "active"
    db_product.last_seen_in_file_id = file_id

    # Suppression des variantes (les prix en cascade via FK) et attributs
    await session.execute(
        delete(ProductVariant).where(ProductVariant.product_id == db_product.id)
    )
    await session.execute(
        delete(Price).where(
            Price.product_id == db_product.id,
            Price.variant_id.is_(None),
        )
    )
    await session.execute(
        delete(ProductAttribute).where(ProductAttribute.product_id == db_product.id)
    )

    await _insert_variants_and_prices(session, p, db_product.id, file_id)
    await _insert_attributes(session, p, db_product.id)
    session.add(ProductHistory(
        product_id=db_product.id,
        change_type=change_type,
        field_changes=d.field_changes or None,
        source_file_id=file_id,
    ))


async def _soft_delete_product(
    session: AsyncSession,
    d: ProductDelta,
    supplier_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    result = await session.execute(
        select(Product).where(
            Product.supplier_id == supplier_id,
            Product.supplier_product_code == d.supplier_product_code,
        )
    )
    db_product = result.scalar_one_or_none()
    if db_product is None:
        return
    db_product.status = "inactive"
    session.add(ProductHistory(
        product_id=db_product.id,
        change_type="DELETE",
        source_file_id=file_id,
    ))


async def _insert_variants_and_prices(
    session: AsyncSession,
    product: ProductPivot,
    product_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    for variant in product.variants:
        db_variant = ProductVariant(
            product_id=product_id,
            variant_dimension=variant.variant_dimension,
            variant_value=variant.variant_value,
            variant_code=variant.variant_code,
            display_order=variant.display_order,
        )
        session.add(db_variant)
        await session.flush()
        for price in variant.prices:
            session.add(_make_price(price, product_id, db_variant.id, file_id))

    for price in product.prices:
        session.add(_make_price(price, product_id, None, file_id))


def _make_price(
    price: PricePivot,
    product_id: uuid.UUID,
    variant_id: uuid.UUID | None,
    file_id: uuid.UUID,
) -> Price:
    return Price(
        product_id=product_id,
        variant_id=variant_id,
        price_type=price.price_type,
        amount=price.amount,
        currency=price.currency,
        tier_min_quantity=price.tier_min_quantity,
        tier_max_quantity=price.tier_max_quantity,
        tier_unit=price.tier_unit,
        valid_from=price.valid_from,
        valid_to=price.valid_to,
        source_file_id=file_id,
    )


async def _insert_attributes(
    session: AsyncSession,
    product: ProductPivot,
    product_id: uuid.UUID,
) -> None:
    for attr in product.attributes:
        session.add(ProductAttribute(
            product_id=product_id,
            attribute_key=attr.key,
            attribute_value=attr.value,
            data_type=attr.data_type,
            unit=attr.unit,
        ))


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
