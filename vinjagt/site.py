"""Bygger den statiske hjemmeside: kopierer web/ og skriver data/*.json."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime

from .config import ROOT
from .matching import group_wines
from .scoring import evaluate

log = logging.getLogger(__name__)


def _iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes") if ts else None


def build(db, cfg: dict, retailers_cfg: list[dict]) -> dict:
    t0 = time.time()
    wines = db.current_wines()
    groups = group_wines(wines)
    history = db.all_history()
    ext = db.ext_ratings()
    rmap = {r["id"]: r for r in retailers_cfg}
    deals, stats = evaluate(wines, groups, history, ext, rmap, cfg["scoring"])
    deals = deals[: int(cfg["site"]["max_deals"])]

    # forhandleroversigt
    db_r = {r["id"]: r for r in db.retailers()}
    deal_count: dict[str, int] = {}
    for d in deals:
        if not d.get("of"):
            continue
        deal_count[d["r"]] = deal_count.get(d["r"], 0) + 1
    shops = []
    for r in retailers_cfg:
        x = db_r.get(r["id"], {})
        shops.append({
            "id": r["id"], "name": r["name"], "url": r["url"], "city": r.get("city"), "note": r.get("note"),
            "enabled": r.get("enabled", True), "platform": x.get("platform") or r.get("platform"),
            "last_scan": _iso(x.get("last_scan")), "status": x.get("last_status") or ("deaktiveret" if not r.get("enabled", True) else "ikke scannet"),
            "products": x.get("products") or 0, "wines": x.get("wines") or 0, "deals": deal_count.get(r["id"], 0),
            "error": x.get("error"), "offer_products": x.get("offer_products") or 0,
            "offer_pages": json.loads(x.get("offer_pages") or "[]"),
        })

    site = cfg["site_dir"]
    web = os.path.join(ROOT, "web")
    os.makedirs(site, exist_ok=True)
    shutil.copytree(web, site, dirs_exist_ok=True)
    os.makedirs(os.path.join(site, "data"), exist_ok=True)
    meta = {
        "generated": _iso(time.time()), "title": cfg["site"]["title"], "subtitle": cfg["site"]["subtitle"],
        "stats": stats, "shops": len([s for s in shops if s["enabled"]]),
        "sources": sorted({r["s"] for d in deals for r in d["rt"]}),
    }
    _dump(os.path.join(site, "data", "deals.json"), deals)
    _dump(os.path.join(site, "data", "retailers.json"), shops)
    _dump(os.path.join(site, "data", "meta.json"), meta)
    log.info("Side bygget: %d tilbud ud af %d vine på %.1fs", len(deals), stats.get("vine", 0), time.time() - t0)
    return meta


def _dump(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
