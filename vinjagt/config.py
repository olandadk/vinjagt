from __future__ import annotations

import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "data_dir": "data",
    "site_dir": "site",
    "user_agent": "VinjagtBot/1.0 (+privat prissammenligning; kontakt: ændr-mig@example.com)",
    "request_delay": 2.0,
    "timeout": 25,
    "workers": 3,
    "respect_robots": True,
    "max_products_api": 6000,
    "max_pages_generic": 350,
    "redetect_days": 14,
    "scoring": {"min_discount": 0.10, "min_value_points": 3.0, "min_vivino_ratings": 25},
    "ratings": {"vivino": {"enabled": False, "pages_per_band": 4, "every_days": 7}, "csv_dir": "ratings"},
    "site": {"title": "Vinjagt", "subtitle": "Gode vintilbud hos danske webshops", "max_deals": 4000},
    "publish": {"enabled": False, "remote": "", "branch": "pages", "cname": ""},
}


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def load(path: str | None = None) -> dict:
    path = path or os.environ.get("VINJAGT_CONFIG") or os.path.join(ROOT, "config.yaml")
    if not os.path.exists(path):
        path = os.path.join(ROOT, "config.example.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {})
    for key in ("data_dir", "site_dir"):
        if not os.path.isabs(cfg[key]):
            cfg[key] = os.path.join(ROOT, cfg[key])
    csv_dir = cfg["ratings"]["csv_dir"]
    cfg["ratings"]["csv_dir"] = csv_dir if os.path.isabs(csv_dir) else os.path.join(ROOT, csv_dir)
    cfg["retailers_file"] = cfg.get("retailers_file") or os.path.join(ROOT, "retailers.yaml")
    os.makedirs(cfg["data_dir"], exist_ok=True)
    return cfg


def slug(name: str) -> str:
    s = name.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa").replace("&", "og")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def retailers(cfg: dict) -> list[dict]:
    with open(cfg["retailers_file"], encoding="utf-8") as f:
        items = yaml.safe_load(f) or []
    out = []
    for r in items:
        r = dict(r)
        r.setdefault("id", slug(r["name"]))
        r["url"] = r["url"].rstrip("/")
        r.setdefault("enabled", True)
        if r.get("platform") == "auto":
            r["platform"] = None
        out.append(r)
    return out
