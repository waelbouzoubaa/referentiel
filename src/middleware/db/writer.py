from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from middleware.core.logging import get_logger
from middleware.storage.minio_client import upload_raw_file
from middleware.db.models import (
    GeryExport,
    GeryExportLine,
    Price,
    Product,
    ProductAttribute,
    ProductAudit,
    ProductHistory,
    ProductVariant,
    Supplier,
    SupplierFile,
)
from middleware.delta.engine import DeltaResult, ProductDelta
from middleware.exporter.gery import GeryExportResult
from middleware.parser.pivot import PricePivot, ProductPivot
from middleware.parser.table_extractor import compute_business_hash_no_prices

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
    original_filename: str | None = None,
    sharepoint_item_id: str | None = None,
) -> SupplierFile:
    """Retourne le SupplierFile existant pour ce hash ou en crée un nouveau.

    La déduplication par content_hash assure l'idempotence : retraiter le même
    fichier ne crée pas de doublon en base.

    Args:
        original_filename: Nom du fichier tel que vu sur SharePoint (le watcher
            télécharge sous un nom temporaire UUID, sans rapport avec ce nom).
        sharepoint_item_id: Identifiant de l'item SharePoint (drive item id).
    """
    content_hash = _file_hash(file_path)
    filename = original_filename or file_path.name

    existing = await session.execute(
        select(SupplierFile).where(SupplierFile.content_hash == content_hash)
    )
    supplier_file = existing.scalar_one_or_none()
    if supplier_file is not None:
        logger.info(
            "fichier déjà connu — réutilisation du SupplierFile existant",
            filename=filename,
            content_hash=content_hash,
        )
        return supplier_file

    minio_path = await upload_raw_file(file_path, supplier.code, content_hash)

    supplier_file = SupplierFile(
        supplier_id=supplier.id,
        filename=filename,
        sharepoint_item_id=sharepoint_item_id or filename,
        content_hash=content_hash,
        size_bytes=file_path.stat().st_size,
        gcs_path=str(file_path),
        minio_path=minio_path,
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
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """Retourne (known_hashes, known_hashes_no_prices, deleted_codes) pour compute_delta.

    - full : tous les produits du fournisseur sont chargés — les absents du nouveau
      fichier génèrent des DELETEs.
    - incremental : seuls les produits présents dans le nouveau fichier sont chargés —
      les absents ne génèrent pas de DELETEs.
    """
    query = select(
        Product.supplier_product_code,
        Product.business_hash,
        Product.business_hash_no_prices,
        Product.status,
    ).where(Product.supplier_id == supplier_id)

    if upload_mode == "incremental" and incoming_codes:
        query = query.where(Product.supplier_product_code.in_(incoming_codes))

    rows = (await session.execute(query)).all()

    known_hashes = {row[0]: row[1] for row in rows}
    known_hashes_no_prices = {row[0]: row[2] for row in rows}
    deleted_codes = {row[0] for row in rows if row[3] in ("inactive", "deleted")}

    return known_hashes, known_hashes_no_prices, deleted_codes


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

def _compute_field_diff(
    db_product: Product,
    old_prices: list[Price],
    old_attrs: list[ProductAttribute],
    new_product: ProductPivot,
) -> list[str]:
    """Retourne la liste des noms de champs qui ont changé entre l'état DB et le produit entrant."""
    changed: list[str] = []

    # Champs fixes
    if (db_product.designation or "").strip().upper() != (new_product.designation or "").strip().upper():
        changed.append("designation")
    if (db_product.family or "").strip() != (new_product.family or "").strip():
        changed.append("family")
    if (db_product.subfamily or "").strip() != (new_product.subfamily or "").strip():
        changed.append("subfamily")

    # Attributs dynamiques (fonctionne pour tous les fournisseurs)
    old_attr_map = {a.attribute_key: a.attribute_value for a in old_attrs}
    new_attr_map = {a.key: a.value for a in new_product.attributes}
    for key in set(old_attr_map) | set(new_attr_map):
        if old_attr_map.get(key) != new_attr_map.get(key):
            changed.append(f"attr_{key}")

    # Prix (directs + variantes) — un seul champ "price" si quoi que ce soit a changé
    old_prices_set = {(p.price_type, str(p.amount)) for p in old_prices}
    new_prices_set = {(p.price_type, str(p.amount)) for p in new_product.prices}
    new_prices_set |= {(p.price_type, str(p.amount)) for v in new_product.variants for p in v.prices}
    if old_prices_set != new_prices_set:
        changed.append("price")

    return changed


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
        business_hash_no_prices=compute_business_hash_no_prices(p),
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

    # Charger l'état actuel avant écrasement pour calculer le diff (directs + variantes)
    old_prices = (await session.execute(
        select(Price).where(Price.product_id == db_product.id)
    )).scalars().all()
    old_attrs = (await session.execute(
        select(ProductAttribute).where(ProductAttribute.product_id == db_product.id)
    )).scalars().all()
    changed_fields = _compute_field_diff(db_product, old_prices, old_attrs, p)

    db_product.designation = p.designation
    db_product.family = p.family
    db_product.subfamily = p.subfamily
    db_product.business_hash = d.new_hash or ""
    db_product.business_hash_no_prices = compute_business_hash_no_prices(p)
    db_product.status = "active"
    db_product.last_seen_in_file_id = file_id

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

    now = datetime.utcnow()
    session.add(ProductHistory(
        product_id=db_product.id,
        change_type=change_type,
        source_file_id=file_id,
    ))
    for field_name in changed_fields:
        session.add(ProductAudit(
            product_id=db_product.id,
            field_name=field_name,
            changed_at=now,
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
        tier_label=price.tier_label,
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


async def persist_gery_export(
    session: AsyncSession,
    export_result: GeryExportResult,
    supplier_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    """Persiste les exports Gery en base : GeryExport + GeryExportLine + marque product_history."""
    if not export_result.files:
        return

    # Récupère les IDs produits par supplier_product_code (1 seule requête)
    all_spc = {rd.supplier_product_code for f in export_result.files for rd in f.row_details}
    if all_spc:
        rows = (await session.execute(
            select(Product.id, Product.supplier_product_code)
            .where(Product.supplier_id == supplier_id, Product.supplier_product_code.in_(all_spc))
        )).all()
        product_id_map: dict[str, uuid.UUID] = {row[1]: row[0] for row in rows}
    else:
        product_id_map = {}

    for gen_file in export_result.files:
        if gen_file.line_count == 0:
            continue

        gery_export = GeryExport(
            export_kind=gen_file.kind,
            output_path=str(gen_file.path),
            output_hash=gen_file.output_hash,
            line_count=gen_file.line_count,
            status="generated",
        )
        session.add(gery_export)
        await session.flush()

        for line_number, detail in enumerate(gen_file.row_details, start=1):
            product_id = product_id_map.get(detail.supplier_product_code)
            if product_id is None:
                continue
            session.add(GeryExportLine(
                export_id=gery_export.id,
                product_id=product_id,
                derived_code=detail.derived_code,
                payload=detail.payload,
                line_number=line_number,
            ))

        # Marque les lignes d'historique de ce fichier comme exportées. Le fichier
        # NEW_ARTICLE couvre les créations, réactivations et lignes modifiées
        # (Gery distingue création/MAJ par la clé à l'import).
        change_types_par_kind = {
            "NEW_ARTICLE": ["CREATE", "REACTIVATE", "UPDATE", "PRICE_CHANGE"],
        }
        change_types = change_types_par_kind.get(gen_file.kind, [])
        if change_types:
            await session.execute(
                update(ProductHistory)
                .where(
                    ProductHistory.source_file_id == file_id,
                    ProductHistory.exported_at.is_(None),
                    ProductHistory.change_type.in_(change_types),
                )
                .values(exported_at=datetime.utcnow(), exported_in_id=gery_export.id)
            )

    logger.info(
        "exports Gery persistés en base",
        fichiers=len([f for f in export_result.files if f.line_count > 0]),
        lignes=sum(f.line_count for f in export_result.files),
    )


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
