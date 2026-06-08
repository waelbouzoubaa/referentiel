from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from middleware.parser.pivot import ProductPivot
from middleware.parser.table_extractor import compute_business_hash, compute_business_hash_no_prices


class ChangeType(str, Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    PRICE_CHANGE = "PRICE_CHANGE"
    DELETE = "DELETE"
    REACTIVATE = "REACTIVATE"


@dataclass
class ProductDelta:
    """Changement métier détecté pour un produit."""

    change_type: ChangeType
    supplier_product_code: str
    supplier_code: str
    new_product: ProductPivot | None = None
    previous_hash: str | None = None
    new_hash: str | None = None
    field_changes: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeltaResult:
    """Résultat complet de la comparaison entre deux snapshots."""

    creates: list[ProductDelta] = field(default_factory=list)
    updates: list[ProductDelta] = field(default_factory=list)
    price_changes: list[ProductDelta] = field(default_factory=list)
    deletes: list[ProductDelta] = field(default_factory=list)
    reactivates: list[ProductDelta] = field(default_factory=list)
    unchanged: int = 0

    @property
    def total_changes(self) -> int:
        return (
            len(self.creates)
            + len(self.updates)
            + len(self.price_changes)
            + len(self.deletes)
            + len(self.reactivates)
        )

    def all_deltas(self) -> list[ProductDelta]:
        return self.creates + self.reactivates + self.updates + self.price_changes + self.deletes


def compute_delta(
    new_products: list[ProductPivot],
    known_hashes: dict[str, str],
    known_hashes_no_prices: dict[str, str] | None = None,
    deleted_codes: set[str] | None = None,
) -> DeltaResult:
    """Compare un snapshot entrant avec l'état connu en base.

    Args:
        new_products: Produits issus du parsing du nouveau fichier.
        known_hashes: Dict supplier_product_code → business_hash actuel en base.
                      Les codes avec status='inactive'/'deleted' doivent aussi être inclus.
        known_hashes_no_prices: Dict supplier_product_code → business_hash_no_prices
                      actuel en base. Permet de distinguer PRICE_CHANGE (seuls les prix
                      diffèrent) d'UPDATE (un champ métier comme la désignation a changé).
        deleted_codes: Codes actuellement marqués comme supprimés/inactifs en base.
                       Un code qui réapparaît dans new_products génère REACTIVATE.

    Returns:
        DeltaResult avec les listes de changements par type.
    """
    known_hashes_no_prices = known_hashes_no_prices or {}
    deleted_codes = deleted_codes or set()
    result = DeltaResult()

    incoming_codes = {p.supplier_product_code for p in new_products}

    for product in new_products:
        code = product.supplier_product_code
        new_hash = compute_business_hash(product)

        if code not in known_hashes:
            # Jamais vu → CREATE
            result.creates.append(ProductDelta(
                change_type=ChangeType.CREATE,
                supplier_product_code=code,
                supplier_code=product.supplier_code,
                new_product=product,
                new_hash=new_hash,
            ))
        elif code in deleted_codes:
            # Était supprimé, réapparaît → REACTIVATE
            result.reactivates.append(ProductDelta(
                change_type=ChangeType.REACTIVATE,
                supplier_product_code=code,
                supplier_code=product.supplier_code,
                new_product=product,
                previous_hash=known_hashes[code],
                new_hash=new_hash,
            ))
        elif known_hashes[code] == new_hash:
            # Identique → rien
            result.unchanged += 1
        else:
            # Hash différent → déterminer si c'est PRICE_CHANGE ou UPDATE
            change_type, field_changes = _classify_change(
                product, known_hashes_no_prices.get(code), new_hash
            )
            delta = ProductDelta(
                change_type=change_type,
                supplier_product_code=code,
                supplier_code=product.supplier_code,
                new_product=product,
                previous_hash=known_hashes[code],
                new_hash=new_hash,
                field_changes=field_changes,
            )
            if change_type == ChangeType.PRICE_CHANGE:
                result.price_changes.append(delta)
            else:
                result.updates.append(delta)

    # Codes connus absents du nouveau snapshot → DELETE
    active_known = set(known_hashes.keys()) - deleted_codes
    for code in active_known - incoming_codes:
        result.deletes.append(ProductDelta(
            change_type=ChangeType.DELETE,
            supplier_product_code=code,
            supplier_code=new_products[0].supplier_code if new_products else "",
            previous_hash=known_hashes[code],
        ))

    return result


def _classify_change(
    product: ProductPivot,
    old_hash_no_prices: str | None,
    new_hash: str,
) -> tuple[ChangeType, dict[str, Any]]:
    """Détermine si le changement est PRICE_CHANGE ou UPDATE (champ métier).

    Compare le hash sans prix stocké en base à celui du produit entrant :
    - identiques → seuls les prix ont changé → PRICE_CHANGE
    - différents (ou absence de référence pour un produit créé avant cette
      colonne) → un champ métier a changé → UPDATE

    Returns:
        (ChangeType, dict des champs modifiés pour le journal d'historique)
    """
    new_hash_no_prices = compute_business_hash_no_prices(product)

    if old_hash_no_prices is not None and old_hash_no_prices == new_hash_no_prices:
        change_type = ChangeType.PRICE_CHANGE
    else:
        change_type = ChangeType.UPDATE

    field_changes: dict[str, Any] = {
        "business_hash_no_prices": {
            "old": old_hash_no_prices,
            "new": new_hash_no_prices,
        },
    }
    if change_type == ChangeType.PRICE_CHANGE:
        field_changes["prices"] = {
            "new_prices": [
                {"type": p.price_type, "amount": str(p.amount)}
                for p in product.all_prices()
            ],
        }

    return change_type, field_changes
