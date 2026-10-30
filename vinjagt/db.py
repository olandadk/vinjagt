"""SQLite-lager: produkter, prishistorik, sidestatus og eksterne vurderinger."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import date

from .models import Wine

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS retailers(
  id TEXT PRIMARY KEY, name TEXT, url TEXT, platform TEXT, last_scan REAL, last_status TEXT,
  products INTEGER DEFAULT 0, wines INTEGER DEFAULT 0, error TEXT, duration REAL);
CREATE TABLE IF NOT EXISTS wines(
  id INTEGER PRIMARY KEY, retailer_id TEXT, url TEXT UNIQUE, name TEXT, producer TEXT, vintage INTEGER,
  volume_cl REAL, wine_type TEXT, country TEXT, in_stock INTEGER, price_1 REAL, price_6 REAL,
  before_price REAL, before_kind TEXT, critic TEXT, image TEXT, gtin TEXT, wine_key TEXT,
  first_seen REAL, last_seen REAL);
CREATE INDEX IF NOT EXISTS wines_ret ON wines(retailer_id);
CREATE INDEX IF NOT EXISTS wines_key ON wines(wine_key, vintage);
CREATE TABLE IF NOT EXISTS price_history(
  wine_id INTEGER, day TEXT, price_1 REAL, price_6 REAL, before_price REAL, PRIMARY KEY(wine_id, day));
CREATE TABLE IF NOT EXISTS pages(
  url TEXT PRIMARY KEY, retailer_id TEXT, last_fetched REAL, is_product INTEGER, status TEXT);
CREATE TABLE IF NOT EXISTS ext_ratings(
  source TEXT, ext_id TEXT, name TEXT, producer TEXT, vintage INTEGER, wine_type TEXT, rating REAL, scale REAL,
  count INTEGER, price REAL, url TEXT, wine_key TEXT, fetched REAL, PRIMARY KEY(source, ext_id, vintage));
CREATE INDEX IF NOT EXISTS ext_key ON ext_ratings(wine_key);
"""


def url_group(u: str) -> tuple:
    """Grupperer adresser efter form: (første mappe, dybde, filendelse)."""
    from urllib.parse import urlparse
    import re as _re
    path = urlparse(u).path
    segs = [s for s in path.split("/") if s]
    ext = _re.search(r"\.[a-z]{2,5}$", path)
    if len(segs) <= 1:
        return ("*", len(segs), ext.group(0) if ext else "")
    return (segs[0].lower(), len(segs), ext.group(0) if ext else "")


class DB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        for table, col, typ in (("wines", "on_offer", "INTEGER DEFAULT 0"), ("retailers", "offer_pages", "TEXT"),
                                ("retailers", "offer_products", "INTEGER DEFAULT 0")):
            cols = {r[1] for r in self.c.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    def commit(self):
        self.c.commit()

    # ---------------------------------------------- forhandlere
    def upsert_retailer(self, r: dict):
        self.c.execute(
            "INSERT INTO retailers(id,name,url,platform) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url, "
            "platform=COALESCE(excluded.platform, retailers.platform)",
            (r["id"], r["name"], r["url"], r.get("platform")))

    def retailer(self, rid: str):
        return self.c.execute("SELECT * FROM retailers WHERE id=?", (rid,)).fetchone()

    def set_platform(self, rid: str, platform: str | None):
        self.c.execute("UPDATE retailers SET platform=? WHERE id=?", (platform, rid))

    def finish_scan(self, rid: str, status: str, products: int, wines: int, error: str | None, duration: float,
                    info: dict | None = None):
        info = info or {}
        self.c.execute("UPDATE retailers SET last_scan=?, last_status=?, products=?, wines=?, error=?, duration=?, "
                       "offer_pages=?, offer_products=? WHERE id=?",
                       (time.time(), status, products, wines, error, duration,
                        json.dumps(info.get("offer_pages") or []), info.get("offer_products"), rid))
        self.commit()

    def retailers(self):
        return [dict(r) for r in self.c.execute("SELECT * FROM retailers ORDER BY name")]

    # ---------------------------------------------- vine
    def save_wine(self, w: Wine, wine_key: str):
        now = time.time()
        row = self.c.execute("SELECT id FROM wines WHERE url=?", (w.url,)).fetchone()
        vals = (w.retailer_id, w.name, w.producer, w.vintage, w.volume_cl, w.wine_type, w.country, int(w.in_stock),
                w.price_1, w.price_6, w.before_price, w.before_kind, json.dumps(w.critic, ensure_ascii=False),
                w.image, w.gtin, wine_key, int(w.on_offer), now)
        if row:
            wid = row["id"]
            self.c.execute("UPDATE wines SET retailer_id=?,name=?,producer=?,vintage=?,volume_cl=?,wine_type=?,country=?,"
                           "in_stock=?,price_1=?,price_6=?,before_price=?,before_kind=?,critic=?,image=?,gtin=?,wine_key=?,"
                           "on_offer=?,last_seen=? WHERE id=?", vals + (wid,))
        else:
            cur = self.c.execute("INSERT INTO wines(retailer_id,name,producer,vintage,volume_cl,wine_type,country,in_stock,"
                                 "price_1,price_6,before_price,before_kind,critic,image,gtin,wine_key,on_offer,last_seen,url,first_seen) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (w.url, now))
            wid = cur.lastrowid
        self.c.execute("INSERT OR REPLACE INTO price_history(wine_id,day,price_1,price_6,before_price) VALUES(?,?,?,?,?)",
                       (wid, date.today().isoformat(), w.price_1, w.price_6, w.before_price))
        return wid

    def current_wines(self, max_age_days: float = 10):
        """Vine set inden for de sidste `max_age_days` dage."""
        cutoff = time.time() - max_age_days * 86400
        return [dict(r) for r in self.c.execute("SELECT * FROM wines WHERE last_seen>=?", (cutoff,))]

    def history(self, wine_id: int, days: int = 120):
        return [dict(r) for r in self.c.execute(
            "SELECT day, price_1, price_6, before_price FROM price_history WHERE wine_id=? "
            "AND day >= date('now', ?) ORDER BY day", (wine_id, f"-{days} days"))]

    def all_history(self, days: int = 120) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in self.c.execute("SELECT wine_id, day, price_1, price_6, before_price FROM price_history "
                                "WHERE day >= date('now', ?) ORDER BY day", (f"-{days} days",)):
            out.setdefault(r["wine_id"], []).append(dict(r))
        return out

    def set_wine_key(self, wid: int, key: str):
        self.c.execute("UPDATE wines SET wine_key=? WHERE id=?", (key, wid))

    # ---------------------------------------------- sider (generisk crawler)
    def page_order(self, rid: str, priority: list[str], urls: list[str]) -> list[str]:
        """Prioritet: tilbudssider -> aldrig hentet -> ældst hentet. Ikke-produkter springes over i 30 dage."""
        known = {r["url"]: dict(r) for r in self.c.execute(
            "SELECT url,last_fetched,is_product FROM pages WHERE retailer_id=?", (rid,))}
        now = time.time()

        def skip(u):
            k = known.get(u)
            return k is not None and k["is_product"] == 0 and now - (k["last_fetched"] or 0) < 30 * 86400

        # Lær af tidligere kørsler: hvilke slags adresser (første mappe + dybde) er produkter?
        stats: dict[tuple, list[int]] = {}
        for u, k in known.items():
            if k["is_product"] is None:
                continue
            st = stats.setdefault(url_group(u), [0, 0])
            st[0] += k["is_product"]
            st[1] += 1

        def rate(u):
            p, n = stats.get(url_group(u), (0, 0))
            return (p + 1) / (n + 2)

        def dead_group(u):  # mindst 8 forsøg og ingen produkter
            p, n = stats.get(url_group(u), (0, 0))
            return n >= 8 and p == 0

        pri = [u for u in dict.fromkeys(priority) if not skip(u)]
        pset = set(pri)
        rest = [u for u in dict.fromkeys(urls) if u not in pset and not skip(u) and not dead_group(u)]
        rest.sort(key=lambda u: (-round(rate(u), 1), known.get(u, {}).get("last_fetched") or 0))
        return pri + rest

    def known_non_products(self, rid: str) -> set[str]:
        return {r[0] for r in self.c.execute("SELECT url FROM pages WHERE retailer_id=? AND is_product=0", (rid,))}

    def mark_page(self, url: str, rid: str, is_product, status: str):
        self.c.execute("INSERT OR REPLACE INTO pages(url,retailer_id,last_fetched,is_product,status) VALUES(?,?,?,?,?)",
                       (url, rid, time.time(), None if is_product is None else int(is_product), status))

    # ---------------------------------------------- eksterne vurderinger
    def save_ext(self, source: str, ext_id: str, name: str, producer: str | None, vintage: int | None,
                 wine_type: str | None, rating: float, scale: float, count: int | None, price: float | None,
                 url: str | None, wine_key: str):
        self.c.execute("INSERT OR REPLACE INTO ext_ratings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (source, ext_id, name, producer, vintage or 0, wine_type, rating, scale, count, price, url,
                        wine_key, time.time()))

    def ext_ratings(self, max_age_days: float = 120):
        cutoff = time.time() - max_age_days * 86400
        return [dict(r) for r in self.c.execute("SELECT * FROM ext_ratings WHERE fetched>=?", (cutoff,))]
