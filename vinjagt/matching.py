"""Genkend samme vin på tværs af forhandlere og eksterne kilder."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .extract import fold

STOP = set("""
vin wine vino vinho wein roedvin rodvin hvidvin rosevin rose rosevin mousserende boblevin dessertvin hedvin orangevin
red white rotwein weisswein fl flaske flasker stk cl ml l ltr liter magnum x kasse karton tilbud nyhed nyt news
oeko oko oekologisk okologisk organic bio biodynamisk biologico biologique natural naturvin vegan
doc docg aoc aop igt igp dop do doca qba qmp vdp vdt vqa ava wo
the of de du des la le les el los di del della dei da do dos das and og et e y und
""".split())

# Ord der adskiller to ellers ens vine – må ikke være forskellige for at to navne regnes som samme vin
DISTINCT = set("""
riserva reserva reserve gran grand grande cru crus superiore classico premier 1er crianza ripasso appassimento
vieilles vignes brut extra nature zero dosage demi sec doux rose blanc rouge bianco rosso blanco tinto noir noirs
blancs millesime vintage late harvest spatlese auslese kabinett trocken feinherb lbv tawny ruby colheita
andenvin
""".split())

# Andenvine (second wines) bærer ofte slottets navn og bliver ellers til de samme ord som hovedvinen,
# fx "Le C des Carmes Haut-Brion" ~ "Les Carmes Haut-Brion". De får et ekstra skjult ord 'andenvin'.
_SECOND = re.compile(
    r"\b(?:le|la|les)\s+[a-z]\s+(?:de|des|du|d)\b"            # Le C des Carmes, La B de ...
    r"|\bsecond(?:e)?\s+(?:vin|wine)\b|\b2(?:nd|e|eme)\s+(?:vin|wine)\b|\bandenvin\b"
    r"|\b(?:le|la)\s+petite?\s+(?!verdot|sirah|syrah|manseng|chablis|arvine)\w"
    r"|\b(?:pagodes|carruades|clarence|clarte|fiefs|alter\s+ego|reserve\s+de\s+la\s+comtesse|pavillon\s+(?:rouge|blanc))\b"
    r"|\b(?:petit|petite)\s+(?:cheval|mouton|lion|figeac|haut\s+lafitte)\b"
    r"|\bchapelle\s+de\s+la\s+mission\b|\bdame\s+de\s+montrose\b|\bseigneurs\s+de\b|\bforts\s+de\s+latour\b")

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_VOL = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:cl|ml|l|ltr|liter)\b")


def tokens(name: str, producer: str | None = None) -> frozenset[str]:
    t = fold(f"{producer or ''} {name or ''}")
    second = bool(_SECOND.search(re.sub(r"[^a-z0-9]+", " ", t)))
    t = _VOL.sub(" ", t)
    t = _YEAR.sub(" ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    tk = {w for w in t.split() if w not in STOP and not w.isdigit() and len(w) > 1}
    return frozenset(tk | {"andenvin"} if second else tk)


def wine_key(name: str, producer: str | None = None) -> str:
    return " ".join(sorted(tokens(name, producer)))


def similar(a: frozenset[str], b: frozenset[str]) -> float:
    """0..1. Kræver at adskillende ord (riserva, brut, ...) er ens."""
    if not a or not b:
        return 0.0
    if (a ^ b) & DISTINCT:
        return 0.0
    inter = len(a & b)
    jac = inter / len(a | b)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    if small <= big and len(small) >= 3:  # fx producent mangler i det ene navn
        jac = max(jac, 0.6 + 0.4 * len(small) / len(big))
    return jac


class _UF:
    def __init__(self):
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _generic_words() -> frozenset[str]:
    from . import extract as X
    words = set()
    for g, syns in X.GRAPE_SYNONYMS.items():
        for s in [g] + syns:
            words.update(re.sub(r"[^a-z0-9]+", " ", fold(s)).split())
    for app, _ in X.APPELLATION_GRAPE:
        words.update(re.sub(r"[^a-z0-9]+", " ", fold(app)).split())
    for r, (c, al) in X.REGIONS.items():
        for s in [r, c] + al:
            words.update(re.sub(r"[^a-z0-9]+", " ", fold(s)).split())
    words.update("rosso bianco tinto blanco rouge blanc reserva riserva crianza gran classico superiore cru premier "
                 "grand vino vin wine red white rose dry trocken brut sec doux old vines vieilles vignes cuvee "
                 "selection reserve estate".split())
    return frozenset(words)


_GENERIC: frozenset[str] | None = None


def is_generic(tk: frozenset[str]) -> bool:
    """Navne uden producent eller særpræg (kun drue, område og almindelige ord) matches ikke."""
    global _GENERIC
    if _GENERIC is None:
        _GENERIC = _generic_words()
    return len(tk) < 2 or all(t in _GENERIC for t in tk)


def _price_ok(a, b, ratio: float = 3.0) -> bool:
    """Samme vin/årgang/størrelse koster ikke over 3x så meget hos en anden forhandler – så er det to vine."""
    return not (a and b) or max(a, b) / min(a, b) <= ratio


def group_wines(wines: list[dict], threshold: float = 0.82) -> dict[int, int]:
    """Returnerer {wine_id: group_id}. Samme vin (navn + årgang + størrelse) hos forskellige forhandlere."""
    uf = _UF()
    by_gtin: dict[str, int] = {}
    blocks: dict[tuple, list[tuple[int, frozenset]]] = defaultdict(list)
    for w in wines:
        wid = w["id"]
        uf.find(wid)
        g = (w.get("gtin") or "").strip()
        if len(g) >= 8:
            if g in by_gtin:
                uf.union(by_gtin[g], wid)
            else:
                by_gtin[g] = wid
        tk = tokens(w["name"], w.get("producer"))
        if is_generic(tk):
            continue  # fx bare "Barolo" eller "Pinot Noir" – kan ikke sikkert matches med andre vine
        blocks[(w.get("vintage") or 0, round(w.get("volume_cl") or 75), w.get("wine_type"))].append((wid, tk))
    # Prefix-filtrering: to navne med lighed >= threshold deler mindst `need` ord (Jaccard: threshold·|x|,
    # delmængde-reglen i similar(): (threshold-0.6)/0.4·|x|). Så er det nok at indeksere og slå op på de
    # len-need+1 sjældneste ord – samme resultat, men vine der kun deler fx 'chateau' sammenlignes ikke.
    ratio = min(threshold, max(0.0, (threshold - 0.6) / 0.4))
    for items in blocks.values():
        df = Counter(t for _, tk in items for t in tk)
        index: dict[str, list[int]] = defaultdict(list)
        toks = dict(items)
        price = {w["id"]: w.get("price_1") for w in wines}
        for wid, tk in items:
            need = max(1, math.ceil(ratio * len(tk) - 1e-9))
            prefix = sorted(tk, key=lambda t: (df[t], t))[:len(tk) - need + 1]
            cands = set()
            for t in prefix:
                cands.update(index[t])
            for c in cands:
                if similar(tk, toks[c]) >= threshold and _price_ok(price.get(c), price.get(wid)):
                    uf.union(c, wid)
            for t in prefix:
                index[t].append(wid)
    return {w["id"]: uf.find(w["id"]) for w in wines}


class ExtIndex:
    """Hurtigt opslag i eksterne vurderinger (Vivino, CSV, ...)."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.toks = [tokens(r["name"], r.get("producer")) for r in rows]
        self.index: dict[str, list[int]] = defaultdict(list)
        for i, tk in enumerate(self.toks):
            for t in tk:
                self.index[t].append(i)

    def lookup(self, name: str, producer: str | None, vintage: int | None, threshold: float = 0.75) -> list[dict]:
        tk = tokens(name, producer)
        if not tk:
            return []
        # start med de sjældneste ord
        rare = sorted(tk, key=lambda t: len(self.index.get(t, ())))[:3]
        cands = set()
        for t in rare:
            cands.update(self.index.get(t, ()))
        best: dict[str, tuple[float, dict]] = {}
        for i in cands:
            r = self.rows[i]
            rv = r.get("vintage") or 0
            if rv and vintage and rv != vintage:
                continue
            s = similar(tk, self.toks[i])
            if rv and vintage:
                s += 0.05  # præcis årgang foretrækkes
            if s >= threshold and s > best.get(r["source"], (0, None))[0]:
                best[r["source"]] = (s, r)
        return [dict(r, match=round(s, 2)) for s, r in best.values()]
