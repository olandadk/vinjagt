"""Prisalarm: besked til mobilen via ntfy (https://ntfy.sh) om nye tilbud.

Der sendes besked om tilbud, der er startet siden sidste kørsel, og som enten har en høj samlet score
eller matcher en vin på overvågningslisten (alerts.watch i config.yaml). Samme tilbud meldes kun én gang.
Installér ntfy-appen, abonnér på dit emne (alerts.ntfy_topic) – vælg et navn, der er svært at gætte.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta

import requests

from .extract import fold

log = logging.getLogger(__name__)


def _matches(deal: dict, watch: list[dict]) -> dict | None:
    text = fold(f"{deal.get('n') or ''} {deal.get('pr') or ''}")
    for w in watch or []:
        words = fold(str(w.get("q") or "")).split()
        if words and all(x in text for x in words) and (not w.get("max") or deal["p1"] <= float(w["max"])):
            return w
    return None


def pick(deals: list[dict], cfg: dict, since: str) -> list[tuple[dict, str]]:
    """Nye tilbud (startet `since` eller senere), der er værd at melde – med begrundelse."""
    min_score = float(cfg.get("min_score") or 101)
    out = []
    for d in deals:
        if not d.get("of") or (d.get("sd") or "") < since:
            continue
        w = _matches(d, cfg.get("watch"))
        if w:
            out.append((d, f"på din liste ({w.get('q')})"))
        elif d["s"] >= min_score:
            out.append((d, f"score {d['s']}"))
    return out


def send(cfg: dict, site_dir: str, data_dir: str) -> int:
    a = cfg.get("alerts") or {}
    topic = (a.get("ntfy_topic") or "").strip()
    if not topic:
        return 0
    try:
        with open(os.path.join(site_dir, "data", "deals.json"), encoding="utf-8") as f:
            deals = json.load(f)
    except (OSError, ValueError):
        return 0
    state_path = os.path.join(data_dir, "alerts_sent.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            sent: dict[str, float] = json.load(f)
    except (OSError, ValueError):
        sent = {}
    since = (date.today() - timedelta(days=1)).isoformat()
    fresh = [(d, why) for d, why in pick(deals, a, since) if f"{d['u']}|{d['sd']}" not in sent]
    if not fresh:
        return 0
    fresh.sort(key=lambda x: -x[0]["s"])
    shown = fresh[:10]
    lines = [f"{d['n']} – {d['p1']:.0f} kr (−{d['d']:.0f} %, {why})\n{d['u']}" for d, why in shown]
    if len(fresh) > len(shown):
        lines.append(f"… og {len(fresh) - len(shown)} mere på siden")
    server = (a.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": f"Vinjagt: {len(fresh)} nye tilbud".encode("utf-8"), "Tags": "wine_glass"}
    if a.get("site_url"):
        headers["Click"] = a["site_url"]
    try:
        r = requests.post(f"{server}/{topic}", data="\n\n".join(lines).encode("utf-8"), headers=headers, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("Prisalarm kunne ikke sendes: %s", e)
        return 0
    now = time.time()
    sent.update({f"{d['u']}|{d['sd']}": now for d, _ in fresh})
    sent = {k: v for k, v in sent.items() if now - v < 90 * 86400}
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(sent, f)
    log.info("Prisalarm sendt: %d nye tilbud", len(fresh))
    return len(fresh)
