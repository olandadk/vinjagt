from __future__ import annotations

from dataclasses import dataclass, field

from . import extract as X


@dataclass
class Product:
    """Ét produkt som fundet i en webshop (før normalisering)."""
    retailer_id: str
    url: str
    name: str
    price: float | None                 # pris pr. stk. (som solgt)
    before_price: float | None = None   # førpris / normalpris
    before_kind: str | None = None      # 'førpris' | 'vejledende' | 'compare_at'
    producer: str | None = None
    categories: list[str] = field(default_factory=list)
    description: str = ""
    in_stock: bool = True
    image: str | None = None
    qty_tiers: list[tuple[int, float]] = field(default_factory=list)  # (antal, pris pr. flaske)
    sku: str | None = None
    gtin: str | None = None
    country: str | None = None
    vintage: int | None = None
    volume_cl: float | None = None
    critic: list[dict] = field(default_factory=list)


@dataclass
class Wine:
    """Normaliseret vin klar til databasen."""
    retailer_id: str
    url: str
    name: str
    producer: str | None
    vintage: int | None
    volume_cl: float
    wine_type: str | None
    country: str | None
    in_stock: bool
    price_1: float           # pr. 75 cl-flaske, køb af 1
    price_6: float           # pr. 75 cl-flaske, køb af 6
    before_price: float | None
    before_kind: str | None
    critic: list[dict]
    image: str | None
    gtin: str | None


def normalize(p: Product) -> Wine | None:
    """Product -> Wine. Returnerer None hvis det ikke er en (enkelt) vin med pris."""
    if not p.price or not p.name:
        return None
    text = f"{p.name} {' '.join(p.categories)}"
    desc = X.strip_html(p.description)
    wtype = X.classify_type(p.name, p.categories, desc)
    if not X.is_wine(p.name, p.categories, wtype):
        return None

    vol = p.volume_cl or X.parse_volume_cl(p.name) or X.parse_volume_cl(desc[:400]) or 75.0
    if vol < 35 or vol > 300:  # små flasker, bag-in-box mv. springes over
        return None
    pack = X.parse_pack_size(p.name) or 1
    unit = p.price / pack
    tiers = list(p.qty_tiers) or X.parse_quantity_prices(desc, unit)
    if pack >= 6:
        tiers.append((pack, unit))
    before = p.before_price / pack if p.before_price else None
    before_kind = p.before_kind
    if not before:
        before, before_kind = X.parse_before_price(desc, unit)

    factor = 75.0 / vol  # pr. 75 cl
    price_1 = round(unit * factor, 2)
    price_6 = round(X.price_for_n(unit, tiers, 6) * factor, 2)
    critic = p.critic or X.parse_critic_scores(f"{p.name}\n{desc}")
    return Wine(
        retailer_id=p.retailer_id, url=p.url, name=p.name.strip(), producer=(p.producer or None),
        vintage=p.vintage or X.parse_vintage(p.name), volume_cl=vol, wine_type=wtype,
        country=p.country or X.detect_country(text, desc[:800]), in_stock=p.in_stock,
        price_1=price_1, price_6=min(price_6, price_1),
        before_price=round(before * factor, 2) if before and before > unit else None,
        before_kind=before_kind if before and before > unit else None,
        critic=critic, image=p.image, gtin=p.gtin,
    )
