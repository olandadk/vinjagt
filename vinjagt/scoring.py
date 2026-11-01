"""Vurdering af tilbud.

En pris holdes op mod fire referencepriser – i prioriteret rækkefølge af troværdighed:
  1. Markedspris: medianprisen for samme vin hos de andre scannede danske forhandlere.
  2. Egen prishistorik: vinens medianpris hos forhandleren de sidste 90 dage (målt af os selv).
  3. Forhandlerens førpris: skal efter prismærkningsbekendtgørelsens § 9 a være laveste pris de seneste
     30 dage. Krydstjekkes mod vores egen historik; vejledende priser vægtes lavt.
  4. Ekstern markedspris (fx Vivinos danske gennemsnitspris), hvis slået til.

Kvaliteten vurderes på en fælles 100-point skala fra anmeldere/medier (oplyst af forhandleren),
Vivino-brugervurderinger og egne CSV-kilder. 'Værdi' er hvor mange point vinen giver i forhold til
hvad vine til samme pris typisk scorer (log-lineær regression over alle vurderede vine).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from statistics import median

from .extract import to_points
from .matching import ExtIndex

W_BEFORE = {"førpris": 1.0, "compare_at": 0.8, "vejledende": 0.5}
W_MARKET, W_HISTORY, W_EXT_PRICE, W_SUSPECT = 1.0, 0.9, 0.7, 0.3


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _fit(points_prices: list[tuple[float, float]]):
    """points ≈ a + b·ln(pris). Returnerer (a, b) eller None."""
    if len(points_prices) < 15:
        return None
    xs = [math.log(p) for _, p in points_prices]
    ys = [q for q, _ in points_prices]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def evaluate(wines: list[dict], groups: dict[int, int], history: dict[int, dict], ext_rows: list[dict],
             retailers: dict[str, dict], cfg: dict, explain: dict | None = None) -> tuple[list[dict], dict]:
    min_discount = float(cfg.get("min_discount", 0.10))
    min_value = float(cfg.get("min_value_points", 3.0))
    min_vivino_count = int(cfg.get("min_vivino_ratings", 25))

    stock = [w for w in wines if w["in_stock"] and w["price_1"]]
    members: dict[int, list[dict]] = defaultdict(list)
    for w in stock:
        members[groups.get(w["id"], w["id"])].append(w)

    ext = ExtIndex(ext_rows) if ext_rows else None

    # --------------------------------------------------- kvalitet pr. vin
    quality: dict[int, tuple[float | None, list[dict]]] = {}
    sources: dict[tuple[str, bool], dict] = {}
    for w in stock:
        grp = members[groups.get(w["id"], w["id"])]
        # anmelderpoint oplyst af forhandlere – hvis flere forhandlere oplyser det samme, stoler vi mere på det
        seen: dict[tuple, set] = defaultdict(set)
        for m in grp:
            for c in json.loads(m.get("critic") or "[]"):
                seen[(c["source"], c["score"], c["scale"])].add(m["retailer_id"])
        items, num, den = [], 0.0, 0.0
        used: dict[tuple[str, bool], tuple[float, float]] = {}  # (kilde, oplyst af forhandler) -> (skala, point)
        for (src, score, scale), shops in seen.items():
            pts = to_points(score, scale)
            wgt = 0.9 if len(shops) >= 2 else 0.7
            items.append({"s": src, "v": f"{score}/{scale}" if scale != 100 else f"{score} p", "pt": round(pts, 1),
                          "own": True, "n": len(shops)})
            used[(src, True)] = (scale, pts)
            num += wgt * pts
            den += wgt
        ext_price = None
        if ext:
            for r in ext.lookup(w["name"], w.get("producer"), w.get("vintage")):
                if r["source"] == "Vivino" and (r.get("count") or 0) < min_vivino_count:
                    continue
                pts = to_points(r["rating"], r["scale"])
                cnt = r.get("count") or 0
                wgt = (0.5 + 0.5 * _clamp(cnt / 500)) if r["source"] == "Vivino" else 1.0
                items.append({"s": r["source"], "v": f"{r['rating']:.1f}" if r["scale"] == 5 else f"{r['rating']:g}/{r['scale']:g}",
                              "pt": round(pts, 1), "n": cnt or None, "u": r.get("url"), "m": r.get("match"),
                              "yr": r.get("vintage") or None})
                used[(r["source"], False)] = (r["scale"], pts)
                num += wgt * pts
                den += wgt
                if r.get("price") and r["source"] == "Vivino":
                    ext_price = r["price"]
        quality[w["id"]] = (round(num / den, 1) if den else None, items, ext_price)
        for key, (scale, pts) in used.items():  # til oversigten over kvalitetskilder
            u = sources.setdefault(key, {"vine": 0, "pt": 0.0, "scales": set()})
            u["vine"] += 1
            u["pt"] += pts
            u["scales"].add(scale)

    fit = _fit([(q[0], w["price_1"]) for w in stock if (q := quality[w["id"]])[0] is not None])

    # --------------------------------------------------- pris og samlet score
    deals = []
    stats = defaultdict(int)
    for w in stock:
        gid = groups.get(w["id"], w["id"])
        foreign = bool((retailers.get(w["retailer_id"]) or {}).get("foreign"))
        # udenlandske priser bruges ikke som markedspris for danske tilbud (afgift, moms og fragt mangler)
        others = [m for m in members[gid] if m["retailer_id"] != w["retailer_id"]
                  and (foreign or not (retailers.get(m["retailer_id"]) or {}).get("foreign"))]
        others_by_shop: dict[str, dict] = {}
        for m in others:  # én pris pr. forhandler (laveste)
            if m["retailer_id"] not in others_by_shop or m["price_1"] < others_by_shop[m["retailer_id"]]["price_1"]:
                others_by_shop[m["retailer_id"]] = m
        others = list(others_by_shop.values())
        p1, p6 = w["price_1"], w["price_6"] or w["price_1"]
        refs: list[tuple[str, float, float]] = []
        flags = []
        market_1 = median([m["price_1"] for m in others]) if others else None
        market_6 = median([m["price_6"] or m["price_1"] for m in others]) if others else None
        if market_1:
            refs.append(("markedspris", market_1, W_MARKET))

        hist = history.get(w["id"]) or {}
        if hist.get("n", 0) >= 14:
            refs.append(("egen prishistorik", hist["med90"], W_HISTORY))
        if w.get("before_price"):
            wgt = W_BEFORE.get(w.get("before_kind") or "førpris", 0.6)
            if hist.get("n30", 0) >= 5 and hist["min30"] < w["before_price"] * 0.97:
                wgt = W_SUSPECT
                flags.append("førpris ikke bekræftet")
            refs.append(("vejl. pris" if w.get("before_kind") == "vejledende" else "førpris", w["before_price"], wgt))
        q, ratings, ext_price = quality[w["id"]]
        if ext_price:
            refs.append(("Vivino DK-pris", ext_price, W_EXT_PRICE))

        best_disc, best_ref, raw_disc = 0.0, None, 0.0
        for label, ref, wgt in refs:
            d = (ref - p1) / ref if ref else 0
            if wgt * d > best_disc:
                best_disc, best_ref, raw_disc = wgt * d, label, d
        if p6 < p1 * 0.9:  # forhandlerens egen 1-flaskepris – svag reference (kan være sat højt)
            d6 = (p1 - p6) / p1 * 0.3
            if d6 > best_disc:
                best_disc, best_ref, raw_disc = d6, "egen 1-flaskepris (køb 6)", (p1 - p6) / p1
        if market_6 and p6 < market_6:
            d6 = (market_6 - p6) / market_6 * 0.9
            if d6 > best_disc:
                best_disc, best_ref, raw_disc = d6, "markedspris v. 6 fl.", (market_6 - p6) / market_6
        if p6 < p1 * 0.9:
            flags.append(f"6-flaskestilbud: spar {round((p1 - p6) / p1 * 100)} %")
        if others and p1 <= min(m["price_1"] for m in others):
            flags.append("billigst i DK")

        value = None
        if q is not None and fit:
            a, b = fit
            value = round(q - (a + b * math.log(p1)), 1)

        is_deal = best_disc >= min_discount or (value is not None and value >= min_value and q >= 88)
        if explain is not None and w["id"] in explain:
            explain[w["id"]] = {"refs": [(lbl, round(ref, 2), wgt) for lbl, ref, wgt in refs], "best_disc": best_disc,
                                "best_ref": best_ref, "q": q, "value": value, "is_deal": is_deal, "others": len(others),
                                "min_discount": min_discount}
        stats["vine"] += 1
        if not is_deal:
            continue
        stats["tilbud"] += 1

        disc_n = _clamp(best_disc / 0.40)
        if q is None:
            qual_n, val_n = 0.3, 0.35
        else:
            qual_n = _clamp((q - 80) / 15)
            val_n = _clamp(0.5 + (value or 0) / 8)
        score = round(100 * (0.45 * disc_n + 0.35 * val_n + 0.20 * qual_n))
        cheapest = min(others, key=lambda m: m["price_1"]) if others else None
        deals.append({
            "id": w["id"], "n": w["name"], "pr": w.get("producer"), "y": w.get("vintage"), "t": w.get("wine_type"),
            "c": w.get("country"), "r": w["retailer_id"], "u": w["url"], "img": w.get("image"),
            "vol": w.get("volume_cl"), "p1": p1, "p6": p6, "bp": w.get("before_price"), "bk": w.get("before_kind"),
            "m1": round(market_1, 2) if market_1 else None, "mn": len(others),
            "ch": {"r": cheapest["retailer_id"], "p": cheapest["price_1"], "u": cheapest["url"]} if cheapest else None,
            "ref": best_ref, "d": round(raw_disc * 100, 1), "de": round(best_disc * 100, 1),
            "q": q, "rt": ratings, "v": value, "s": score, "fl": flags,
            "fs": w.get("first_seen"), "of": bool(w.get("on_offer")), "g": gid,
            "sd": hist.get("since"), "lo": hist.get("lo90"), "h": hist.get("weeks"),
            "fx": (retailers.get(w["retailer_id"]) or {}).get("country") if foreign else None,
        })
    deals.sort(key=lambda d: -d["s"])
    stats["med_vurdering"] = sum(1 for d in deals if d["q"] is not None)
    stats["regression"] = {"a": round(fit[0], 2), "b": round(fit[1], 2)} if fit else None
    stats["kilder"] = [{"s": s, "own": own, "vine": u["vine"], "pt": round(u["pt"] / u["vine"], 1),
                        "skala": sorted(u["scales"])} for (s, own), u in sources.items()]
    return deals, dict(stats)
