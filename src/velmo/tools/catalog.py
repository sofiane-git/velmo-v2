"""Outils de catalogue : disponibilité et stock."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..db import Product, ProductVariant
from ._common import select


def check_stock(session: Session, product_ref: str, size: str) -> dict[str, Any]:
    """Indique si une référence est disponible dans une taille donnée (stock souvent 1)."""
    product = session.get(Product, product_ref)
    if product is None:
        return {"error": "unknown_product", "product_ref": product_ref}
    variant = session.scalars(
        select(ProductVariant).where(
            ProductVariant.product_ref == product_ref, ProductVariant.size == size
        )
    ).first()
    if variant is None:
        return {"product_ref": product_ref, "size": size, "available": False, "stock": 0}
    return {
        "product_ref": product_ref,
        "title": product.title,
        "size": size,
        "available": variant.stock > 0,
        "stock": variant.stock,
    }
