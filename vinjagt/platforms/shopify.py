"""Shopify: offentlig /products.json giver hele kataloget uden at skulle hente hver produktside."""
from __future__ import annotations

import re
from typing import Iterator

from .. import extract as X
from ..models import Product


def detect(client, base: str) -> bool:
    data, _ = client.get_json(f"{base}/products.json?limit=1")
    return isinstance(data, dict) and isinstance(data.get("products"), list)


def _qty_from_variant(title: str) -> int | None:
    t = (title or "").lower()
    if t in ("default title", "", "1", "1 stk", "1 fl", "1 flaske", "enkelt flaske", "enkeltflaske"):
        return 1
    m = re.search(r"(\d{1,2})\s*(?:stk|fl|flasker|x|-?pak|pack|kasse)", t) or re.search(r"(?:kasse|karton|ramme)\D*(\d{1,2})", t)
    return int(m.group(1)) if m else None


def scan(client, retailer: dict, max_products: int = 5000) -> Iterator[Product]:
    base = retailer["url"].rstrip("/")
    page, seen = 1, 0
    while seen < max_products:
        data, resp = client.get_json(f"{base}/products.json?limit=250&page={page}")
        if not data or not data.get("products"):
            break
        for p in data["products"]:
            seen += 1
            variants = p.get("variants") or []
            if not variants:
                continue
            singles, tiers = [], []
            for v in variants:
                price = X.parse_price(v.get("price"))
                if not price:
                    continue
                q = _qty_from_variant(v.get("title") or "")
                if q and q > 1:
                    tiers.append((q, round(price / q, 2)))
                elif q == 1 or len(variants) == 1:
                    singles.append(v)
            if not singles:  # kun kasser/flere-pakninger -> brug den første variant
                singles = [variants[0]]
            v = min(singles, key=lambda v: X.parse_price(v.get("price")) or 1e9)
            price = X.parse_price(v.get("price"))
            compare = X.parse_price(v.get("compare_at_price"))
            tags = p.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]
            cats = [c for c in [p.get("product_type")] + list(tags) if c]
            img = (p.get("images") or [{}])[0].get("src") if p.get("images") else None
            yield Product(
                retailer_id=retailer["id"],
                url=f"{base}/products/{p.get('handle')}",
                name=p.get("title") or "",
                price=price,
                before_price=compare if compare and price and compare > price else None,
                before_kind="førpris" if compare and price and compare > price else None,
                producer=p.get("vendor") if p.get("vendor") and p.get("vendor").lower() not in (retailer.get("name", "").lower(),) else None,
                categories=cats,
                description=p.get("body_html") or "",
                in_stock=any(x.get("available", True) for x in variants),
                image=img,
                qty_tiers=[t for t in tiers if price and t[1] <= price],
                sku=v.get("sku"),
                gtin=v.get("barcode") or None,
                on_offer=bool(compare and price and compare > price),
            )
        if len(data["products"]) < 250:
            break
        page += 1
