"""WooCommerce: den offentlige Store API (/wp-json/wc/store/v1/products) kræver ingen nøgle."""
from __future__ import annotations

from typing import Iterator

from .. import extract as X
from ..models import Product

API = "/wp-json/wc/store/v1/products"


def detect(client, base: str) -> bool:
    data, _ = client.get_json(f"{base}{API}?per_page=1")
    return isinstance(data, list) and (not data or "prices" in data[0])


def _money(prices: dict, key: str) -> float | None:
    raw = prices.get(key)
    if raw in (None, "", "0"):
        return None
    try:
        minor = int(prices.get("currency_minor_unit", 2))
        return int(raw) / (10 ** minor)
    except (TypeError, ValueError):
        return X.parse_price(raw)


def scan(client, retailer: dict, max_products: int = 5000) -> Iterator[Product]:
    base = retailer["url"].rstrip("/")
    page, seen, total_pages = 1, 0, None
    while seen < max_products:
        data, resp = client.get_json(f"{base}{API}?per_page=100&page={page}")
        if not isinstance(data, list) or not data:
            break
        if total_pages is None:
            try:
                total_pages = int(resp.headers.get("X-WP-TotalPages", "0")) or None
            except ValueError:
                total_pages = None
        for p in data:
            seen += 1
            prices = p.get("prices") or {}
            price = _money(prices, "price")
            regular = _money(prices, "regular_price")
            cats = [c.get("name", "") for c in p.get("categories") or []] + [t.get("name", "") for t in p.get("tags") or []]
            attrs = {a.get("name", "").lower(): ", ".join(t.get("name", "") for t in a.get("terms") or [])
                     for a in p.get("attributes") or []}
            producer = next((v for k, v in attrs.items() if k in ("producent", "producer", "vingård", "vingaard", "brand", "mærke")), None)
            country = next((v for k, v in attrs.items() if k in ("land", "country", "oprindelsesland")), None)
            vintage = next((v for k, v in attrs.items() if k in ("årgang", "aargang", "vintage")), None)
            img = (p.get("images") or [{}])[0].get("src") if p.get("images") else None
            desc = (p.get("short_description") or "") + "\n" + (p.get("description") or "")
            yield Product(
                retailer_id=retailer["id"],
                url=p.get("permalink") or "",
                name=X.strip_html(p.get("name") or ""),
                price=price,
                before_price=regular if regular and price and regular > price else None,
                before_kind="førpris" if regular and price and regular > price else None,
                producer=producer,
                categories=[c for c in cats if c] + [v for v in attrs.values() if v][:6],
                description=desc,
                in_stock=bool(p.get("is_in_stock", True)),
                image=img,
                sku=p.get("sku") or None,
                country=X.detect_country(country) if country else None,
                vintage=X.parse_vintage(vintage) if vintage else None,
                on_offer=bool(p.get("on_sale")) or bool(regular and price and regular > price),
            )
        page += 1
        if total_pages and page > total_pages:
            break
