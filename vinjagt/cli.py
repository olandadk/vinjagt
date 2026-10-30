"""Kommandolinje:  python -m vinjagt <kommando>

  run        scan + vurderinger + byg side + udgiv   (det systemd-timeren kører)
  scan       scan forhandlere (--only id1,id2)
  detect     find platform for hver forhandler og vis resultatet
  ratings    hent/indlæs eksterne vurderinger (Vivino hvis slået til, CSV-filer)
  build      byg hjemmesiden fra databasen
  publish    skub den byggede side til git-remote
  serve      vis siden lokalt på http://<pi>:8080
  status     vis status for hver forhandler
  offers     vis hvilke tilbudssider der findes hos hver forhandler
  diagnose   vis hvorfor produktsider ikke giver vine (pr. forhandler)
  debug URL  hent én side og vis hvad scanneren ser
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config as C
from .db import DB
from .http import PoliteClient
from .matching import wine_key
from .models import normalize
from .platforms import PLATFORMS, detect

log = logging.getLogger("vinjagt")


def _client(cfg) -> PoliteClient:
    return PoliteClient(cfg["user_agent"], delay=float(cfg["request_delay"]), timeout=float(cfg["timeout"]),
                        respect_robots=bool(cfg["respect_robots"]))


def _scan_one(cfg, r: dict, platform: str | None, db_for_pages) -> tuple:
    t0 = time.time()
    client = _client(cfg)
    try:
        try:  # hurtigt tjek af om shoppen overhovedet svarer
            client.s.head(r["url"], timeout=min(15, float(cfg["timeout"])), allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            return r, platform, [], 0, f"ikke tilgængelig ({type(e).__name__})", time.time() - t0, {}
        if not platform:
            platform = detect(client, r["url"])
        if not platform:
            return r, None, [], 0, "ingen understøttet platform/sitemap fundet", time.time() - t0, {}
        mod = PLATFORMS[platform]
        info: dict = {}
        if platform == "generic":
            limit = int(r.get("max_pages") or cfg["max_pages_generic"])
            bg = int(r.get("background_pages", cfg["background_pages"]))
            it = mod.scan(client, r, limit, db=db_for_pages, background=bg, info=info)
        else:
            it = mod.scan(client, r, int(cfg["max_products_api"]), info=info)
        wines, n = [], 0
        for p in it:
            n += 1
            w = normalize(p)
            if w:
                wines.append(w)
        info["offer_products"] = sum(1 for w in wines if w.on_offer)
        return r, platform, wines, n, None, time.time() - t0, info
    except Exception as e:  # noqa: BLE001
        log.exception("%s fejlede", r["id"])
        return r, platform, [], 0, f"{type(e).__name__}: {e}", time.time() - t0, {}


class _LockedDB:
    """Tråd-sikker indpakning til side-rotation i den generiske crawler."""

    def __init__(self, db, lock):
        self.db, self.lock = db, lock

    def page_order(self, *a):
        with self.lock:
            return self.db.page_order(*a)

    def known_non_products(self, *a):
        with self.lock:
            return self.db.known_non_products(*a)

    def mark_page(self, *a):
        with self.lock:
            self.db.mark_page(*a)


def cmd_scan(cfg, db, only: set[str] | None = None, force_detect=False):
    import threading
    lock = threading.Lock()
    rs = [r for r in C.retailers(cfg) if r["enabled"] and (not only or r["id"] in only)]
    jobs = []
    for r in rs:
        db.upsert_retailer(r)
        row = db.retailer(r["id"])
        plat = r.get("platform") or (None if force_detect else (row["platform"] if row else None))
        if plat and not r.get("platform") and row and row["last_scan"] and \
                time.time() - row["last_scan"] > cfg["redetect_days"] * 86400 and row["last_status"] != "ok":
            plat = None
        jobs.append((r, plat))
    db.commit()
    total = 0
    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as ex:
        futs = [ex.submit(_scan_one, cfg, r, plat, _LockedDB(db, lock)) for r, plat in jobs]
        for f in as_completed(futs):
            r, platform, wines, n, err, dur, info = f.result()
            with lock:
                if platform:
                    db.set_platform(r["id"], platform)
                for w in wines:
                    db.save_wine(w, wine_key(w.name, w.producer))
                status = "ok" if not err and wines else ("fejl" if err else "ingen vine fundet")
                db.finish_scan(r["id"], status, n, len(wines), err, dur, info)
            total += len(wines)
            log.info("%-28s %-11s %5d produkter, %5d vine, %4d på tilbud (%.0fs)%s", r["name"], platform or "-", n,
                     len(wines), info.get("offer_products") or 0, dur,
                     f"  FEJL: {err}" if err else "")
    log.info("Scanning færdig: %d vine i alt", total)


def cmd_detect(cfg, db):
    client = _client(cfg)
    for r in C.retailers(cfg):
        p = r.get("platform") or detect(client, r["url"])
        db.upsert_retailer(r)
        db.set_platform(r["id"], p)
        db.commit()
        print(f"{r['name']:30} {p or 'IKKE FUNDET':12} {r['url']}")


def cmd_ratings(cfg, db, force=False):
    from .ratings import csvfile, vivino
    if os.path.isdir(cfg["ratings"]["csv_dir"]):
        csvfile.load(db, cfg["ratings"]["csv_dir"])
    v = cfg["ratings"]["vivino"]
    if v.get("enabled"):
        stamp = os.path.join(cfg["data_dir"], ".vivino_last")
        last = os.path.getmtime(stamp) if os.path.exists(stamp) else 0
        if force or time.time() - last > float(v.get("every_days", 7)) * 86400:
            vivino.harvest(_client(cfg), db, v)
            open(stamp, "w").close()


def cmd_status(cfg, db):
    ids = {r["id"] for r in C.retailers(cfg)}
    for r in [x for x in db.retailers() if x["id"] in ids]:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_scan"])) if r["last_scan"] else "-"
        print(f"{r['name']:26} {r['platform'] or '-':12} {r['last_status'] or '-':10} {r['products'] or 0:5} produkter {r['wines'] or 0:5} vine "
              f"{r['offer_products'] or 0:5} tilbud  {ts}  {r['error'] or ''}")


def cmd_offers(cfg, only=None):
    """Vis hvilke tilbudssider scanneren finder hos hver forhandler (uden at hente produkterne)."""
    from urllib.parse import urlparse

    from .platforms import generic
    import os

    from .db import DB
    db = DB(os.path.join(cfg["data_dir"], "vinjagt.sqlite"))
    client = _client(cfg)
    for r in C.retailers(cfg):
        if only and r["id"] not in only:
            continue
        row = db.retailer(r["id"])
        plat = r.get("platform") or (row["platform"] if row else None)
        if plat == "woocommerce":
            from .platforms import woocommerce
            info = {}
            n = len(woocommerce.offer_ids(client, r["url"].rstrip("/"), r, info))
            print(f"{r['name']} (woocommerce): varer markeret 'på udsalg'" + (f" + {n} i tilbudskategori" if n else ""))
            for p in info.get("offer_pages") or []:
                print(f"   {p}")
            continue
        if plat == "shopify":
            from .platforms import shopify
            info: dict = {}
            n = len(shopify.offer_handles(client, r["url"].rstrip("/"), r, info))
            print(f"{r['name']} (shopify): {n} varer i tilbudssamlinger + varer med førpris")
            for p in info.get("offer_pages") or []:
                print(f"   {p}")
            continue
        sm = generic.sitemap_urls(client, r["url"]) if plat == "generic" else []
        pages = list(r.get("offer_urls") or []) + generic.discover_offer_pages(
            client, r["url"], urlparse(r["url"]).netloc, sitemap_urls_=sm)
        print(f"{r['name']} ({plat or 'platform ukendt endnu'}):")
        for p in pages or ["  (ingen fundet – tilføj offer_urls i retailers.yaml)"]:
            print(f"   {p}")


def cmd_diagnose(cfg, db, only=None):
    """Hvorfor giver produktsider ingen vine? Optælling pr. forhandler + eksempler."""
    for r in C.retailers(cfg):
        if only and r["id"] not in only:
            continue
        rows = db.c.execute("SELECT status, is_product, COUNT(*) n FROM pages WHERE retailer_id=? "
                            "GROUP BY status, is_product ORDER BY n DESC", (r["id"],)).fetchall()
        if not rows:
            continue
        print(f"\n{r['name']}:")
        for x in rows:
            label = {1: "produkt læst", 0: "ikke produkt"}.get(x["is_product"], "fejl")
            print(f"   {x['n']:5}  {x['status']:<20} {label}")
        ex = db.c.execute("SELECT url FROM pages WHERE retailer_id=? AND is_product=0 LIMIT 2", (r["id"],)).fetchall()
        for e in ex:
            print(f"          eksempel (ikke produkt): {e['url']}")
        for st in ("ingen produktdata", "error"):
            ex = db.c.execute("SELECT url FROM pages WHERE retailer_id=? AND status=? LIMIT 2", (r["id"], st)).fetchall()
            for e in ex:
                print(f"          eksempel ({st}): {e['url']}")
        ex = db.c.execute("SELECT url, status FROM pages WHERE retailer_id=? AND status NOT IN "
                          "('ok','ingen produktdata','error','robots') LIMIT 2", (r["id"],)).fetchall()
        for e in ex:
            print(f"          eksempel (HTTP {e['status']}): {e['url']}")


def cmd_debug_auto(cfg, db, only):
    """Vælg selv sider fra databasen, der ikke gav produktdata, og vis hvad scanneren ser."""
    for rid in only:
        rows = db.c.execute("SELECT url FROM pages WHERE retailer_id=? AND (is_product=0 OR is_product IS NULL) "
                            "ORDER BY (url LIKE '%/vin%' OR url LIKE '%wine%' OR url LIKE '%produkt%') DESC, RANDOM() LIMIT 2",
                            (rid,)).fetchall()
        if not rows:
            print(f"{rid}: ingen sider uden produktdata i databasen")
        for row in rows:
            print(f"\n===== {rid}: {row['url']}")
            try:
                cmd_debug(cfg, row["url"])
            except Exception as e:  # noqa: BLE001
                print(f"fejl: {e}")


def cmd_debug(cfg, url: str):
    """Hent én side og vis hvad scanneren ser."""
    import json as _json
    import re as _re

    from bs4 import BeautifulSoup

    from .models import normalize
    from .platforms import generic
    client = _client(cfg)
    print(f"robots.txt tillader: {client.allowed(url)}")
    r = client.get(url)
    print(f"HTTP {r.status_code}  Content-Type: {r.headers.get('Content-Type')!r}  {len(r.content)} bytes")
    if r.url != url:
        print(f"omdirigeret til: {r.url}")
    soup = BeautifulSoup(r.text, "html.parser")
    print(f"<title>: {soup.title.get_text(strip=True)[:100] if soup.title else '-'}")
    h1 = soup.find("h1")
    print(f"<h1>: {h1.get_text(' ', strip=True)[:100] if h1 else '-'}")
    blocks = soup.find_all("script", type=_re.compile("ld\\+json", _re.I))
    print(f"JSON-LD blokke: {len(blocks)}")
    for b in blocks[:6]:
        raw = (b.string or b.get_text() or "").strip()
        try:
            data = _json.loads(raw)
            types = sorted({str(n.get('@type')) for n in generic._walk(data) if isinstance(n, dict) and n.get('@type')})
            print(f"   typer: {', '.join(types)[:150]}")
        except ValueError as e:
            print(f"   ugyldig JSON ({e}): {raw[:120]!r}")
    for sel in ('meta[property="product:price:amount"]', '[itemprop="price"]', 'meta[property="og:price:amount"]'):
        el = soup.select_one(sel)
        if el:
            print(f"{sel}: {el.get('content') or el.get_text(strip=True)[:40]}")
    if _re.search(r"(18 år|alder|age verif|verify.*age)", r.text[:20000], _re.I):
        print("NB: siden nævner aldersbekræftelse")
    p = generic.parse_product_page(r.text, r.url, "debug")
    print(f"\nResultat: {p.name if p else 'INGEN PRODUKT FUNDET'}")
    if p:
        print(f"   pris {p.price}  førpris {p.before_price} ({p.before_kind})  mængdepriser {p.qty_tiers}")
        print(f"   kategorier {p.categories[:5]}  producent {p.producer}")
        w = normalize(p)
        print(f"   som vin: {'nej (ikke vin / ingen pris)' if not w else f'{w.wine_type}, {w.country}, {w.vintage}, 1 fl. {w.price_1} / 6 fl. {w.price_6}'}")


def cmd_serve(cfg, port: int):
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=cfg["site_dir"])
    print(f"Serverer {cfg['site_dir']} på http://0.0.0.0:{port}")
    http.server.ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vinjagt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "scan", "detect", "ratings", "build", "publish", "serve", "status", "offers", "diagnose", "debug"])
    ap.add_argument("url", nargs="?", help="URL til 'debug'")
    ap.add_argument("--only", help="kommasepareret liste af forhandler-id'er")
    ap.add_argument("--config")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--redetect", action="store_true", help="find platform igen for alle forhandlere")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    cfg = C.load(a.config)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(os.path.join(cfg["data_dir"], "vinjagt.log"), encoding="utf-8")],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if a.command == "serve":
        return cmd_serve(cfg, a.port)
    db = DB(os.path.join(cfg["data_dir"], "vinjagt.sqlite"))
    only = set(a.only.split(",")) if a.only else None
    from . import publish, site
    if a.command in ("run", "scan"):
        cmd_scan(cfg, db, only, a.redetect)
    if a.command == "detect":
        cmd_detect(cfg, db)
    if a.command in ("run", "ratings"):
        cmd_ratings(cfg, db, force=a.command == "ratings")
    if a.command in ("run", "build"):
        site.build(db, cfg, C.retailers(cfg))
    if a.command in ("run", "publish"):
        try:
            publish.publish(cfg)
        except Exception as e:  # noqa: BLE001
            log.error("Udgivelse fejlede: %s", e)
    if a.command == "status":
        cmd_status(cfg, db)
    if a.command == "offers":
        cmd_offers(cfg, only)
    if a.command == "diagnose":
        cmd_diagnose(cfg, db, only)
    if a.command == "debug":
        if a.url:
            cmd_debug(cfg, a.url)
        elif only:
            cmd_debug_auto(cfg, db, sorted(only))
        else:
            print("Brug: debug URL   eller   debug --only forhandler-id")


if __name__ == "__main__":
    main()
