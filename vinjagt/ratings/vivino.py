"""Vivino – brugervurderinger og dansk markedspris.

Henter Vivinos 'explore'-liste for det danske marked (country_code=DK, DKK) side for side
og gemmer navn, årgang, gennemsnitsvurdering, antal vurderinger og pris lokalt.
Vinene fra forhandlerne matches derefter lokalt – der sendes altså ikke én forespørgsel pr. vin.

BEMÆRK: Vivino har ingen officiel offentlig API, og deres brugervilkår begrænser automatiseret
adgang. Kilden er derfor slået FRA som standard (ratings.vivino.enabled i config.yaml), og den
henter kun et begrænset antal sider pr. kørsel. Slå den kun til, hvis du har vurderet vilkårene.
"""
from __future__ import annotations

import logging

from ..matching import wine_key

log = logging.getLogger(__name__)

API = "https://www.vivino.com/api/explore/explore"
TYPES = {1: "rød", 2: "hvid", 3: "mousserende", 4: "rosé", 7: "dessert", 24: "dessert"}
PRICE_BANDS = [(0, 80), (80, 120), (120, 180), (180, 300), (300, 600), (600, 5000)]


def _year(v) -> int:
    try:
        y = int(v)
        return y if 1900 < y < 2100 else 0
    except (TypeError, ValueError):
        return 0


def harvest(client, db, cfg: dict) -> int:
    pages = int(cfg.get("pages_per_band", 4))
    saved = 0
    for type_id, wtype in TYPES.items():
        for lo, hi in PRICE_BANDS:
            for page in range(1, pages + 1):
                params = {
                    "country_code": "DK", "currency_code": "DKK", "min_rating": "1", "order_by": "ratings_count",
                    "order": "desc", "page": page, "price_range_min": lo, "price_range_max": hi,
                    "wine_type_ids[]": type_id, "language": "da",
                }
                try:
                    data, r = client.get_json(API, params=params)
                except Exception as e:  # noqa: BLE001
                    log.warning("Vivino: %s", e)
                    return saved
                matches = ((data or {}).get("explore_vintage") or {}).get("matches") or []
                if not matches:
                    break
                for m in matches:
                    v = m.get("vintage") or {}
                    w = v.get("wine") or {}
                    stats = v.get("statistics") or {}
                    rating, count = stats.get("ratings_average"), stats.get("ratings_count") or 0
                    ws = w.get("statistics") or {}
                    if (not rating or count < 25) and ws.get("ratings_average"):
                        rating, count, year = ws.get("ratings_average"), ws.get("ratings_count") or 0, 0
                    else:
                        year = _year(v.get("year"))
                    if not rating:
                        continue
                    producer = (w.get("winery") or {}).get("name")
                    name = w.get("name") or v.get("name") or ""
                    price = (m.get("price") or {}).get("amount")
                    db.save_ext("Vivino", str(w.get("id")), name, producer, year, wtype, float(rating), 5,
                                int(count), float(price) if price else None,
                                f"https://www.vivino.com/w/{w.get('id')}" + (f"?year={year}" if year else ""),
                                wine_key(name, producer))
                    saved += 1
                db.commit()
    log.info("Vivino: %d vurderinger gemt", saved)
    return saved
