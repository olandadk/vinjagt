"""Tekst-parsing: priser, mængderabat, årgang, flaskestørrelse, vintype, land og anmelderpoint.

Alt her er rene funktioner uden netværk, så de er lette at teste.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable

# ---------------------------------------------------------------- priser

_NUM = r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"


def parse_price(value) -> float | None:
    """'1.299,95' / '1299.95' / '149,-' / 149 -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    s = str(value).strip().replace("\xa0", " ")
    s = re.sub(r"(kr\.?|dkk|,-|\.-)", "", s, flags=re.I).strip()
    m = re.search(r"\d[\d.\s]*(?:,\d+)?|\d+(?:\.\d+)?", s)
    if not m:
        return None
    t = m.group(0).replace(" ", "")
    if "," in t:  # dansk format: punktum = tusind, komma = decimal
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"\d{1,3}\.\d{3}", t):
        t = t.replace(".", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def _p(s: str) -> float | None:
    return parse_price(s)


# Mængderabat. Returnerer liste af (antal, pris pr. flaske).
_QTY_PATTERNS = [
    # "6 stk. for 499" / "6 fl. kun 499 kr" / "3 for 300"  (totalpris)
    (re.compile(r"\b(\d{1,2})\s*(?:stk\.?|fl\.?|flasker|flaske|x)?\s*(?:for|kun|samlet)\s*(?:kr\.?\s*)?" + _NUM, re.I), "total"),
    # "ved køb af 6 stk.: 89,95 pr. stk" / "ved køb af 6: 89 kr/stk"
    (re.compile(r"ved\s+køb\s+af\s+(?:min\.?\s*)?(\d{1,2})\s*(?:stk\.?|fl\.?|flasker)?\s*[:\-–]?\s*(?:kun\s*)?(?:kr\.?\s*)?" + _NUM + r"\s*(?:kr\.?)?\s*(?:pr\.?|/)\s*(?:stk|fl|flaske)", re.I), "unit"),
    # "89,95 kr. pr. fl. ved 6 stk" / "89 kr/stk. v. 6 fl."
    (re.compile(_NUM + r"\s*(?:kr\.?)?\s*(?:pr\.?|/)\s*(?:stk|fl|flaske)\.?\s*(?:ved|v\.)\s*(?:køb\s+af\s+)?(?:min\.?\s*)?(\d{1,2})", re.I), "unit_rev"),
    # "Pris v. 6 stk. 89,95" / "Pris ved 6 fl: 89"
    (re.compile(r"pris\s+(?:ved|v\.)\s*(\d{1,2})\s*(?:stk\.?|fl\.?|flasker)?\s*[:\-–]?\s*(?:kr\.?\s*)?" + _NUM, re.I), "unit"),
    # "6+ flasker 109,00" / "6 fl.: 89,95" / "12 flasker á 79" -> pr. flaske eller total (afgøres af størrelsen)
    (re.compile(r"(?<![\d,.])(\d{1,2})\s*\+?\s*(?:stk\.?|fl\.?|flasker|flaske)\s*[:\-–]?\s*(?:á|a|à|kun)?\s*(?:(?:kr\.?|dkk)\s*)?" + _NUM, re.I), "auto"),
    # "ab 6 Flaschen je 9,90 €" / "ab 6 Flaschen: 9,90" (tysk)
    (re.compile(r"ab\s+(\d{1,2})\s*(?:flaschen|fl\.?|stück|stk\.?)\s*[:\-–]?\s*(?:je|à|a)?\s*(?:€|eur)?\s*" + _NUM, re.I), "unit"),
    # "Kassepris (6 fl.) 499" -> total
    (re.compile(r"kassepris\s*\(?\s*(\d{1,2})\s*(?:stk\.?|fl\.?|flasker)?\s*\)?\s*[:\-–]?\s*(?:kr\.?\s*)?" + _NUM, re.I), "total"),
]


def parse_quantity_prices(text: str, unit_price: float | None = None) -> list[tuple[int, float]]:
    """Finder mængdepriser i fri tekst. Filtrerer urealistiske resultater fra."""
    if not text:
        return []
    found: list[tuple[int, float]] = []
    for rx, kind in _QTY_PATTERNS:
        for m in rx.finditer(text):
            if kind == "unit_rev":
                price, qty = _p(m.group(1)), int(m.group(2))
            else:
                qty, price = int(m.group(1)), _p(m.group(2))
            if not price or qty < 2 or qty > 24:
                continue
            if kind == "auto":
                if not unit_price:
                    continue
                per = price if price <= unit_price * 1.0001 else price / qty
            else:
                per = price / qty if kind == "total" else price
            if unit_price:
                # en mængdepris skal ligge mellem 25 % og 100 % af stykprisen
                if not (0.25 * unit_price <= per <= unit_price * 1.0001):
                    continue
            found.append((qty, round(per, 2)))
    # dedup – laveste pris pr. antal
    best: dict[int, float] = {}
    for q, p in found:
        best[q] = min(p, best.get(q, p))
    return sorted(best.items())


_SINGLE_RX = re.compile(r"(?<![\d,.+])1\s*(?:stk\.?|fl\.?|flaske)(?!\w)\s*[:\-–]?\s*(?:(?:kr\.?|dkk)\s*)?" + _NUM, re.I)


def parse_single_price(text: str) -> float | None:
    """'1 flaske 324,95' / '1 stk.: 99,95 kr' -> pris for én flaske."""
    m = _SINGLE_RX.search(text or "")
    return _p(m.group(1)) if m else None


def price_for_n(unit_price: float | None, tiers: Iterable[tuple[int, float]], n: int = 6) -> float | None:
    """Pris pr. flaske når man køber n flasker af samme vin."""
    if unit_price is None:
        return None
    best = unit_price
    for q, p in tiers:
        if q <= n:
            best = min(best, p)
    return round(best, 2)


_BEFORE_RX = re.compile(
    r"(før\s*pris|førpris|normalpris|normal\s+pris|laveste\s+pris\s+(?:de\s+)?(?:sidste|seneste)\s+30\s+dage|før|vejl\.?\s*(?:udsalgs)?pris|vejledende\s+(?:udsalgs)?pris|statt|bisher|uvp|unverbindliche\s+preisempfehlung)\s*:?\s*(?:kr\.?\s*)?"
    + _NUM,
    re.I,
)


def parse_before_price(text: str, current: float | None) -> tuple[float | None, str | None]:
    """Returnerer (førpris, type) hvor type er 'førpris' eller 'vejledende'."""
    if not text:
        return None, None
    best: tuple[float | None, str | None] = (None, None)
    for m in _BEFORE_RX.finditer(text):
        label = m.group(1).lower()
        v = _p(m.group(2))
        if not v or (current and (v <= current or v > current * 2.5)):
            continue
        kind = "vejledende" if ("vejl" in label or "uvp" in label or "preisempf" in label) else "førpris"
        if best[0] is None or (kind == "førpris" and best[1] != "førpris"):
            best = (v, kind)
    return best


# ---------------------------------------------------------------- årgang og størrelse

def parse_vintage(text: str) -> int | None:
    if not text:
        return None
    for m in re.finditer(r"(?<![\d,.])(19[4-9]\d|20[0-3]\d)(?!\d|[,.]\d|\s*(?:ml|cl|år\b|years))", text):
        y = int(m.group(1))
        if 1945 <= y <= 2035:
            return y
    return None


def parse_volume_cl(text: str) -> float | None:
    if not text:
        return None
    t = text.lower()
    if re.search(r"\bjeroboam\b|\bdouble magnum\b", t):
        return 300.0
    if "magnum" in t:
        return 150.0
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(cl|ml|ltr|liter|l)\b", t)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    if unit == "ml":
        v /= 10
    elif unit in ("l", "ltr", "liter"):
        v *= 100
    return v if 5 <= v <= 1800 else None


def parse_pack_size(text: str) -> int | None:
    """'6 x 75 cl' / '6 fl. i kasse' / 'kasse med 6' -> 6"""
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\b(\d{1,2})\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:cl|ml|l)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(?:kasse|karton|ramme)\s*(?:med|á|a|m\.)\s*(\d{1,2})\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{1,2})\s*(?:fl\.?|flasker|stk\.?)\s*(?:i\s+)?(?:kasse|karton|ramme)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\(\s*(\d{1,2})\s*(?:fl\.?|flasker|stk\.?|bottles)\s*\)", t)  # "(3 fl.)"
    if m and int(m.group(1)) > 1:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- normalisering

def fold(s: str) -> str:
    """Små bogstaver, uden accenter (æøå bevares som ae/oe/aa)."""
    s = s.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


@lru_cache(maxsize=None)
def _word_pattern(w: str) -> str:
    return " " + fold(w) + " "


@lru_cache(maxsize=512)  # samme tekst slås op mod mange ordlister i træk (classify_type, detect_country ...)
def _word_text(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "


def has_word(text: str, words: Iterable[str]) -> str | None:
    t = _word_text(text)
    for w in words:
        if _word_pattern(w) in t:
            return w
    return None


# ---------------------------------------------------------------- tilbudsmærker

OFFER_TAGS = ["tilbud", "på tilbud", "paa tilbud", "sale", "on sale", "udsalg", "kampagne", "nedsat", "spotpris",
              "black friday", "black week", "outlet", "restsalg", "lagersalg", "ugens tilbud", "månedens tilbud"]


def has_offer_tag(tags) -> bool:
    return any(has_word(t, OFFER_TAGS) for t in tags or [] if t)


# ---------------------------------------------------------------- ikke-vin

NON_WINE = [
    "whisky", "whiskey", "gin", "rom", "rum", "vodka", "cognac", "armagnac", "calvados", "grappa", "tequila", "mezcal",
    "likoer", "likør", "liqueur", "snaps", "akvavit", "aquavit", "brandy", "bitter", "absinth", "pastis", "ouzo",
    "oel", "øl", "beer", "ipa", "lager", "pilsner", "stout", "cider", "mjoed", "mjød", "sodavand", "tonic", "juice",
    "vand", "kaffe", "te", "chokolade", "oliven", "olivenolie", "eddike", "pasta", "salami", "skinke", "ost",
    "glas", "vinglas", "weinglas", "likör", "bier", "geschenkgutschein", "gutschein", "probierpaket", "weinpaket",
    "geschenkset", "präsent", "praesent", "karaffe", "korkenzieher", "kühler", "karaffel", "dekanter", "proptraekker", "proptrækker", "vinkoeler", "vinkøler", "vinreol",
    "vinkoeleskab", "vinkøleskab", "gavekort", "gaveaeske", "gaveæske", "gavekurv", "gavepose", "smagning",
    "vinsmagning", "arrangement", "billet", "kursus", "bog", "fragt", "pant", "trae kasse", "trækasse",
    "smagekasse", "vinpakke", "mixkasse", "blandet kasse", "abonnement", "vinklub", "alkoholfri", "alcohol free",
    "0,0%", "alkoholfrit", "spritz", "tea", "negroni", "cocktail", "limoncello", "limoneto", "kombucha",
    "bag in box", "bib", "certifikat", "certificate", "collector", "collectors",
]

# ---------------------------------------------------------------- vintype

TYPES = ["rød", "hvid", "rosé", "mousserende", "dessert", "orange"]

_EXPLICIT = [
    ("mousserende", ["mousserende", "sparkling", "schaumwein", "sekt", "winzersekt", "perlwein", "crémant de", "spumante", "champagne", "cava", "prosecco", "cremant", "crémant", "franciacorta",
                     "spumante", "sekt", "pet nat", "pét nat", "petnat", "pet-nat", "frizzante", "lambrusco", "boblevin", "bobler",
                     "brut", "extra brut", "brut nature"]),
    ("dessert", ["portvin", "port", "suesswein", "süßwein", "dessertwein", "likörwein", "sherry", "madeira", "marsala", "sauternes", "tokaji", "tokaj", "dessertvin", "hedvin",
                 "vin santo", "icewine", "ice wine", "eiswein", "banyuls", "maury", "pedro ximenez", "beerenauslese",
                 "trockenbeerenauslese", "vin doux naturel", "passito", "recioto", "late harvest", "moscatel de setubal", "barsac"]),
    ("orange", ["orangevin", "orange wine", "orange vin", "skin contact", "skinkontakt", "ramato", "amber wine"]),
    ("rosé", ["rosé", "rose", "rosevin", "rosévin", "roséwein", "rosewein", "weissherbst", "weißherbst", "rosato", "rosado", "blush", "clairet"]),
    ("hvid", ["hvidvin", "hvid", "white wine", "white", "blanc", "bianco", "blanco", "weisswein", "weißwein", "weiss", "weiß", "branco"]),
    ("rød", ["roedvin", "rødvin", "rød", "red wine", "red", "rouge", "rosso", "tinto", "rotwein", "rot"]),
]

_GRAPES = {
    "rød": ["cabernet sauvignon", "cabernet franc", "cabernet", "merlot", "pinot noir", "spatburgunder", "spätburgunder",
            "syrah", "shiraz", "malbec", "tempranillo", "sangiovese", "nebbiolo", "barolo", "barbaresco", "brunello",
            "chianti", "rioja", "ribera del duero", "amarone", "ripasso", "valpolicella", "primitivo", "zinfandel",
            "grenache", "garnacha", "cannonau", "monastrell", "mourvedre", "carmenere", "carménère", "pinotage",
            "nero d avola", "montepulciano", "aglianico", "negroamaro", "dolcetto", "barbera", "gamay", "beaujolais",
            "chateauneuf du pape", "châteauneuf du pape", "cotes du rhone", "côtes du rhône", "saint emilion", "pauillac",
            "margaux", "medoc", "médoc", "pomerol", "priorat", "toro", "douro", "dao", "touriga", "blaufrankisch",
            "blaufränkisch", "zweigelt", "corvina", "petit verdot", "tannat", "mencia", "mencía", "bobal", "cinsault",
            "carignan", "gigondas", "vacqueyras", "cahors", "madiran", "bourgogne rouge", "taurasi", "bolgheri",
            "super tuscan", "appassimento", "crozes hermitage", "cote rotie", "côte rôtie", "saint joseph", "cornas"],
    "hvid": ["chardonnay", "sauvignon blanc", "riesling", "pinot grigio", "pinot gris", "pinot blanc", "chenin blanc",
             "chenin", "albarino", "albariño", "alvarinho", "gruner veltliner", "grüner veltliner", "gruner", "grüner",
             "viognier", "gewurztraminer", "gewürztraminer", "semillon", "sémillon", "muscadet", "sancerre",
             "pouilly fume", "pouilly fumé", "pouilly fuisse", "pouilly fuissé", "chablis", "meursault", "soave",
             "vermentino", "verdejo", "rueda", "godello", "gavi", "cortese", "fiano", "greco", "falanghina", "trebbiano",
             "garganega", "verdicchio", "pecorino", "torrontes", "torrontés", "marsanne", "roussanne", "picpoul",
             "vinho verde", "furmint", "assyrtiko", "macabeo", "viura", "silvaner", "muller thurgau", "müller thurgau",
             "kerner", "moscato", "muscat", "arneis", "ribolla", "friulano", "colombard", "gros manseng", "entre deux mers",
             "petit chablis", "vouvray", "savennieres", "savennières", "condrieu", "grillo", "catarratto", "inzolia",
             "txakoli", "assyrtiko", "solaris", "bourgogne blanc", "macon", "mâcon"],
}

COUNTRIES = {
    "Frankrig": ["frankrig", "france", "francia", "bordeaux", "bourgogne", "burgundy", "champagne", "rhone", "rhône",
                 "loire", "alsace", "languedoc", "roussillon", "provence", "beaujolais", "chablis", "sancerre", "cahors",
                 "chateauneuf du pape", "châteauneuf du pape", "pauillac", "saint emilion", "medoc", "médoc", "margaux"],
    "Italien": ["italien", "italy", "italia", "piemonte", "piedmont", "toscana", "tuscany", "veneto", "puglia", "sicilien",
                "sicilia", "sardinien", "abruzzo", "barolo", "barbaresco", "chianti", "brunello", "amarone", "valpolicella",
                "prosecco", "bolgheri", "montalcino", "friuli", "lombardia", "franciacorta", "campania", "marche", "alto adige"],
    "Spanien": ["spanien", "spain", "espana", "españa", "rioja", "ribera del duero", "priorat", "rias baixas", "rías baixas",
                "rueda", "toro", "jumilla", "navarra", "penedes", "penedès", "cava", "jerez", "bierzo", "la mancha", "valencia", "galicia"],
    "Portugal": ["portugal", "douro", "dao", "dão", "alentejo", "vinho verde", "madeira", "porto", "lisboa", "bairrada", "setubal"],
    "Tyskland": ["tyskland", "germany", "deutschland", "mosel", "rheingau", "pfalz", "rheinhessen", "nahe", "baden", "franken"],
    "Østrig": ["oestrig", "østrig", "austria", "osterreich", "österreich", "wachau", "kamptal", "burgenland", "kremstal", "steiermark"],
    "USA": ["usa", "californien", "california", "napa", "sonoma", "oregon", "washington state", "paso robles", "lodi"],
    "Chile": ["chile", "concha y toro", "maipo", "colchagua", "casablanca", "maule", "aconcagua", "rapel"],
    "Argentina": ["argentina", "mendoza", "salta", "uco valley", "patagonien", "patagonia"],
    "Australien": ["australien", "australia", "barossa", "mclaren vale", "margaret river", "coonawarra", "yarra", "clare valley"],
    "New Zealand": ["new zealand", "newzealand", "marlborough", "central otago", "hawkes bay", "hawke s bay"],
    "Sydafrika": ["sydafrika", "south africa", "stellenbosch", "swartland", "western cape", "paarl", "franschhoek"],
    "Ungarn": ["ungarn", "hungary", "tokaj", "tokaji", "villany"],
    "Grækenland": ["graekenland", "grækenland", "greece", "santorini", "nemea", "naoussa"],
    "Danmark": ["dansk vin", "danish wine", "danmark", "denmark"],  # kun fra navn/kategorier – se detect_country
    "Libanon": ["libanon", "lebanon", "bekaa"],
    "Georgien": ["georgien", "georgia", "kakheti"],
    "Slovenien": ["slovenien", "slovenia"],
    "Kroatien": ["kroatien", "croatia"],
    "Uruguay": ["uruguay"],
    "Canada": ["canada", "ontario", "okanagan", "niagara", "british columbia"],
    "Israel": ["israel"],
    "England": ["england", "english sparkling"],
    "Schweiz": ["schweiz", "switzerland"],
    "Moldova": ["moldova"],
    "Rumænien": ["rumaenien", "rumænien", "romania"],
}


def classify_type(name: str, categories: Iterable[str] = (), description: str = "") -> str | None:
    """Vintype ud fra kategorier/tags først, derefter navn, derefter druer/områder."""
    cats = " | ".join(categories or [])
    # tydelige ord i selve navnet (fx "Orangevin", "Rosé", "Champagne") vinder over kategorierne
    for wtype, words in _EXPLICIT[:4]:
        w = has_word(name, words)
        if w and not (wtype == "rosé" and w == "rose") and not (wtype == "dessert" and w == "port"):
            return wtype
    for source in (cats, name):
        if not source:
            continue
        for wtype, words in _EXPLICIT:
            w = has_word(source, words)
            if not w:
                continue
            # 'blanc de noirs', 'pinot blanc' osv. er ikke nødvendigvis hvid i sig selv – men tæt nok på.
            if wtype == "rosé" and w == "rose" and has_word(source, ["rose des vents", "la rose", "rosemount"]):
                continue
            if wtype == "dessert" and w == "port" and source is name and not has_word(
                    source, ["ruby", "tawny", "lbv", "late bottled", "vintage port", "colheita", "porto"]):
                continue
            if wtype == "rød" and w == "rot" and source is name:
                continue
            return wtype
    text = f"{name} {cats} {description[:600] if description else ''}"
    for wtype in ("rød", "hvid"):
        if has_word(text, _GRAPES[wtype]):
            return wtype
    return None


PRODUCER_COUNTRY = {"concha y toro": "Chile", "bodega norton": "Argentina"}


def detect_country(*texts: str, exclude: Iterable[str] = ()) -> str | None:
    for text in texts:
        if not text:
            continue
        t = " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "
        for producer, country in PRODUCER_COUNTRY.items():
            if f" {producer} " in t:
                return country
        for country, words in COUNTRIES.items():
            if country in exclude:
                continue
            if has_word(text, words):
                return country
    return None


# Blandede kasser og pakker – også som sammensatte ord ("efterårssmagskassen", "Spritz-pakken", "6 økologiske vine")
MIXED_RX = re.compile(r"smag[se]?kasse|smagepakke|vinpakke|vinkasse|blandet\s+kasse|mixkasse|-?pakken\b|"
                      r"kassen\b|\bkasse med\b|\bpakke\b|\bmix\b|\bsampler\b|\b\d\s*x\s*\d{2,3}\s*p\b|\b\d{1,2}\s+(?:[a-zæøå]+\s+){0,2}vine\b|probierpaket|weinpaket", re.I)


def is_wine(name: str, categories: Iterable[str] = (), wine_type: str | None = None) -> bool:
    cats = " ".join(categories or [])
    if has_word(name, NON_WINE) or MIXED_RX.search(name):
        return False
    if wine_type:
        return True
    # uden vintype: kun vin hvis kategori/navn siger vin
    return bool(has_word(f"{name} {cats}", ["vin", "wine", "vino", "vinho", "wein"]))


# ---------------------------------------------------------------- anmeldelser oplyst på produktsiden

CRITICS = [
    "Robert Parker", "Wine Advocate", "Parker", "James Suckling", "Suckling", "Wine Spectator", "Decanter", "Vinous",
    "Antonio Galloni", "Jancis Robinson", "Falstaff", "Gambero Rosso", "Tim Atkin", "Jeb Dunnuck", "Wine Enthusiast",
    "Wine & Spirits", "Guía Peñín", "Guia Penin", "Peñín", "Penin", "Luca Maroni", "Bibenda", "Doctor Wine",
    "Vinum", "Gault Millau", "Gault&Millau", "Neal Martin", "Stephen Tanzer", "Bettane & Desseauve", "RVF",
    "Revue du Vin de France", "Jasper Morris", "Allen Meadows", "Burghound", "Halliday", "James Halliday",
    "Wine Enthusiast", "Andreas Larsson", "Wine Align", "Vinoble",
]
DK_MEDIA = ["Politiken", "Berlingske", "Børsen", "Borsen", "Jyllands-Posten", "Jyllandsposten", "JP", "Ekstra Bladet",
            "BT", "B.T.", "Weekendavisen", "Vinbladet", "Smagsdommerne", "Information", "Vinavisen", "Fyens Stiftstidende",
            "Altinget", "Femina", "Euroman", "Dansk Vinguide"]

_crit_alt = "|".join(sorted((re.escape(c) for c in CRITICS), key=len, reverse=True))
_media_alt = "|".join(sorted((re.escape(c) for c in DK_MEDIA), key=len, reverse=True))

_CRIT_RX = [
    # "93 point Robert Parker" / "93 p. James Suckling" / "93/100 Decanter"
    re.compile(r"(?<!\d)(\d{2,3})(?:\s*[-–]\s*\d{2,3})?\s*(?:/\s*100|point|points|pts|p\.)\s*(?:fra|i|af|by|from|-|–|:)?\s*(" + _crit_alt + r")", re.I),
    # "James Suckling: 93" / "Robert Parker 93 point"
    re.compile(r"(" + _crit_alt + r")\s*[:\-–(]?\s*(\d{2,3})(?:\s*[-–]\s*\d{2,3})?\s*(?:/\s*100|point|points|pts|p\b|\))?", re.I),
]
_STAR_RX = [
    # "5 stjerner i Politiken" / "6/6 stjerner Berlingske"
    re.compile(r"(?<!\d)([1-6])(?:\s*(?:/\s*6|ud\s+af\s+6))?\s*(?:stjerner|stjerne|hjerter|vinglas|glas|druer)\s*(?:i|fra|af|-|–|:)?\s*(" + _media_alt + r")\b", re.I),
    re.compile(r"\b(" + _media_alt + r")\s*[:\-–]?\s*([1-6])\s*(?:/\s*6\s*)?(?:stjerner|stjerne|hjerter|vinglas|glas)", re.I),
]


def _canon_critic(name: str) -> str:
    n = name.strip()
    low = fold(n)
    mapping = {
        "parker": "Robert Parker", "wine advocate": "Robert Parker", "robert parker": "Robert Parker",
        "suckling": "James Suckling", "james suckling": "James Suckling", "penin": "Guía Peñín", "guia penin": "Guía Peñín",
        "halliday": "James Halliday", "gault&millau": "Gault Millau", "borsen": "Børsen", "jp": "Jyllands-Posten",
        "jyllandsposten": "Jyllands-Posten", "b.t.": "BT", "revue du vin de france": "RVF",
    }
    if low in mapping:
        return mapping[low]
    canon = {fold(c): c for c in CRITICS + DK_MEDIA}
    canon.update({"vinavisen": "Vinavisen", "vinous": "Vinous", "luca maroni": "Luca Maroni"})
    return canon.get(low, n)


def parse_critic_scores(text: str) -> list[dict]:
    """Anmelderpoint nævnt i produktteksten. Returnerer [{'source','score','scale'}]."""
    if not text:
        return []
    out: dict[str, dict] = {}
    for i, rx in enumerate(_CRIT_RX):
        for m in rx.finditer(text):
            score, critic = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            s = int(score)
            if not (80 <= s <= 100):
                continue
            c = _canon_critic(critic)
            out[c] = {"source": c, "score": max(s, out.get(c, {}).get("score", 0)), "scale": 100}
    for i, rx in enumerate(_STAR_RX):
        for m in rx.finditer(text):
            stars, media = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            c = _canon_critic(media)
            out[c] = {"source": c, "score": int(stars), "scale": 6}
    return list(out.values())


def to_points(score: float, scale: float) -> float:
    """Fælles 100-point skala."""
    if scale == 100:
        return float(score)
    if scale == 6:  # danske aviser: 6 → 95, 5 → 90, 4 → 85, 3 → 80
        return 65 + 5 * float(score)
    if scale == 5:  # Vivino-lignende: 4.0 → 90, 3.5 → 85, 4.5 → 95
        return 50 + 10 * float(score)
    if scale == 20:  # Jancis Robinson m.fl.: 17 → 92, 16 → 89
        return 41 + 3 * float(score)
    return float(score) / scale * 100


def strip_html(html: str | None) -> str:
    if not html:
        return ""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&aelig;", "æ").replace("&oslash;", "ø")
         .replace("&aring;", "å").replace("&#8211;", "–").replace("&quot;", '"'))
    return re.sub(r"[ \t\r\f\v]+", " ", t).strip()


# ---------------------------------------------------------------- drue og region

# Kanonisk druenavn -> synonymer (flest ord først matches før kortere, fx "Cabernet Franc" før "Cabernet")
GRAPE_SYNONYMS = {
    "Cabernet Sauvignon": ["cabernet sauvignon", "cab sauv", "cabernet"],
    "Cabernet Franc": ["cabernet franc", "bouchet"],
    "Merlot": ["merlot"],
    "Pinot Noir": ["pinot noir", "spätburgunder", "spatburgunder", "pinot nero", "blauburgunder"],
    "Syrah": ["syrah", "shiraz"],
    "Grenache": ["grenache", "garnacha", "garnatxa", "cannonau"],
    "Mourvèdre": ["mourvedre", "mourvèdre", "monastrell", "mataro"],
    "Tempranillo": ["tempranillo", "tinta roriz", "aragonez", "tinto fino", "tinta de toro", "cencibel"],
    "Sangiovese": ["sangiovese", "brunello", "prugnolo gentile", "morellino"],
    "Nebbiolo": ["nebbiolo", "spanna", "chiavennasca"],
    "Barbera": ["barbera"],
    "Dolcetto": ["dolcetto"],
    "Corvina": ["corvina", "corvinone"],
    "Primitivo": ["primitivo", "zinfandel"],
    "Negroamaro": ["negroamaro"],
    "Nero d'Avola": ["nero d avola", "nero d'avola", "calabrese"],
    "Nerello Mascalese": ["nerello mascalese", "nerello"],
    "Aglianico": ["aglianico"],
    "Montepulciano": ["montepulciano d abruzzo", "montepulciano d'abruzzo"],
    "Malbec": ["malbec", "côt", "cot"],
    "Carménère": ["carmenere", "carménère"],
    "Pinotage": ["pinotage"],
    "Gamay": ["gamay"],
    "Cinsault": ["cinsault", "cinsaut"],
    "Carignan": ["carignan", "carignano", "cariñena", "mazuelo", "samsó"],
    "Tannat": ["tannat"],
    "Touriga Nacional": ["touriga nacional", "touriga"],
    "Mencía": ["mencia", "mencía"],
    "Bobal": ["bobal"],
    "Blaufränkisch": ["blaufrankisch", "blaufränkisch", "lemberger", "kékfrankos"],
    "Zweigelt": ["zweigelt"],
    "Petit Verdot": ["petit verdot"],
    "Lagrein": ["lagrein"],
    "Teroldego": ["teroldego"],
    "Xinomavro": ["xinomavro"],
    "Agiorgitiko": ["agiorgitiko"],
    "Saperavi": ["saperavi"],
    "Grenache Blanc": ["grenache blanc", "garnacha blanca", "garnatxa blanca"],
    "Chardonnay": ["chardonnay"],
    "Sauvignon Blanc": ["sauvignon blanc", "fumé blanc", "fume blanc", "sauvignon"],
    "Riesling": ["riesling"],
    "Pinot Gris": ["pinot gris", "pinot grigio", "grauburgunder", "ruländer"],
    "Pinot Blanc": ["pinot blanc", "pinot bianco", "weissburgunder", "weißburgunder"],
    "Chenin Blanc": ["chenin blanc", "chenin", "steen"],
    "Grüner Veltliner": ["gruner veltliner", "grüner veltliner", "gruner", "grüner"],
    "Albariño": ["albarino", "albariño", "alvarinho"],
    "Verdejo": ["verdejo"],
    "Godello": ["godello"],
    "Viognier": ["viognier"],
    "Gewürztraminer": ["gewurztraminer", "gewürztraminer", "traminer"],
    "Muscat": ["muscat", "moscato", "moscatel", "muskateller", "muscadelle"],
    "Sémillon": ["semillon", "sémillon"],
    "Melon de Bourgogne": ["melon de bourgogne", "muscadet"],
    "Vermentino": ["vermentino", "rolle", "pigato"],
    "Garganega": ["garganega"],
    "Cortese": ["cortese"],
    "Fiano": ["fiano"],
    "Greco": ["greco di tufo", "greco"],
    "Falanghina": ["falanghina"],
    "Trebbiano": ["trebbiano", "ugni blanc"],
    "Verdicchio": ["verdicchio"],
    "Pecorino": ["pecorino"],
    "Arneis": ["arneis"],
    "Friulano": ["friulano"],
    "Ribolla Gialla": ["ribolla gialla", "ribolla"],
    "Grillo": ["grillo"],
    "Carricante": ["carricante"],
    "Torrontés": ["torrontes", "torrontés"],
    "Marsanne": ["marsanne"],
    "Roussanne": ["roussanne"],
    "Picpoul": ["picpoul", "piquepoul"],
    "Furmint": ["furmint"],
    "Assyrtiko": ["assyrtiko"],
    "Macabeo": ["macabeo", "viura"],
    "Xarel·lo": ["xarel lo", "xarello", "xarel-lo"],
    "Silvaner": ["silvaner", "sylvaner"],
    "Müller-Thurgau": ["muller thurgau", "müller thurgau", "müller-thurgau", "rivaner"],
    "Glera": ["glera"],
    "Solaris": ["solaris"],
    "Rondo": ["rondo"],
}

# Appellationer hvor druen er givet (bruges når druen ikke står i navnet)
APPELLATION_GRAPE = [
    ("vino nobile di montepulciano", "Sangiovese"), ("rosso di montepulciano", "Sangiovese"),
    ("brunello di montalcino", "Sangiovese"), ("rosso di montalcino", "Sangiovese"), ("chianti", "Sangiovese"),
    ("morellino di scansano", "Sangiovese"), ("montefalco rosso", "Sangiovese"),
    ("barolo", "Nebbiolo"), ("barbaresco", "Nebbiolo"), ("roero rosso", "Nebbiolo"), ("gattinara", "Nebbiolo"),
    ("ghemme", "Nebbiolo"), ("langhe nebbiolo", "Nebbiolo"), ("valtellina", "Nebbiolo"),
    ("amarone", "Corvina"), ("valpolicella", "Corvina"), ("ripasso", "Corvina"), ("bardolino", "Corvina"),
    ("taurasi", "Aglianico"), ("aglianico del vulture", "Aglianico"), ("etna rosso", "Nerello Mascalese"),
    ("etna bianco", "Carricante"), ("primitivo di manduria", "Primitivo"), ("salice salentino", "Negroamaro"),
    ("soave", "Garganega"), ("gavi", "Cortese"), ("roero arneis", "Arneis"), ("prosecco", "Glera"),
    ("lugana", "Trebbiano"), ("frascati", "Trebbiano"),
    ("rioja", "Tempranillo"), ("ribera del duero", "Tempranillo"), ("toro", "Tempranillo"),
    ("rias baixas", "Albariño"), ("rías baixas", "Albariño"), ("rueda", "Verdejo"), ("jumilla", "Mourvèdre"),
    ("chablis", "Chardonnay"), ("meursault", "Chardonnay"), ("puligny montrachet", "Chardonnay"),
    ("chassagne montrachet", "Chardonnay"), ("pouilly fuisse", "Chardonnay"), ("pouilly fuissé", "Chardonnay"),
    ("macon", "Chardonnay"), ("mâcon", "Chardonnay"), ("saint veran", "Chardonnay"), ("saint véran", "Chardonnay"),
    ("bourgogne blanc", "Chardonnay"), ("blanc de blancs", "Chardonnay"),
    ("bourgogne rouge", "Pinot Noir"), ("gevrey chambertin", "Pinot Noir"), ("nuits saint georges", "Pinot Noir"),
    ("vosne romanee", "Pinot Noir"), ("vosne romanée", "Pinot Noir"), ("chambolle musigny", "Pinot Noir"),
    ("pommard", "Pinot Noir"), ("volnay", "Pinot Noir"), ("santenay", "Pinot Noir"), ("mercurey", "Pinot Noir"),
    ("hautes cotes de nuits", "Pinot Noir"), ("hautes côtes de nuits", "Pinot Noir"), ("marsannay", "Pinot Noir"),
    ("sancerre rouge", "Pinot Noir"), ("sancerre", "Sauvignon Blanc"), ("pouilly fume", "Sauvignon Blanc"),
    ("pouilly fumé", "Sauvignon Blanc"), ("menetou salon", "Sauvignon Blanc"), ("quincy", "Sauvignon Blanc"),
    ("touraine", "Sauvignon Blanc"), ("vouvray", "Chenin Blanc"), ("savennieres", "Chenin Blanc"),
    ("savennières", "Chenin Blanc"), ("montlouis", "Chenin Blanc"), ("saumur champigny", "Cabernet Franc"),
    ("chinon", "Cabernet Franc"), ("bourgueil", "Cabernet Franc"), ("muscadet", "Melon de Bourgogne"),
    ("beaujolais", "Gamay"), ("morgon", "Gamay"), ("fleurie", "Gamay"), ("moulin a vent", "Gamay"),
    ("moulin-à-vent", "Gamay"), ("brouilly", "Gamay"), ("julienas", "Gamay"), ("juliénas", "Gamay"),
    ("chiroubles", "Gamay"), ("saint amour", "Gamay"), ("regnie", "Gamay"), ("régnié", "Gamay"),
    ("hermitage", "Syrah"), ("crozes hermitage", "Syrah"), ("cote rotie", "Syrah"), ("côte rôtie", "Syrah"),
    ("cornas", "Syrah"), ("saint joseph", "Syrah"), ("condrieu", "Viognier"),
    ("chateauneuf du pape", "Grenache"), ("châteauneuf du pape", "Grenache"), ("gigondas", "Grenache"),
    ("vacqueyras", "Grenache"), ("cotes du rhone", "Grenache"), ("côtes du rhône", "Grenache"), ("rasteau", "Grenache"),
    ("cahors", "Malbec"), ("madiran", "Tannat"),
    ("pauillac", "Cabernet Sauvignon"), ("saint julien", "Cabernet Sauvignon"), ("saint estephe", "Cabernet Sauvignon"),
    ("saint estèphe", "Cabernet Sauvignon"), ("margaux", "Cabernet Sauvignon"), ("haut medoc", "Cabernet Sauvignon"),
    ("haut médoc", "Cabernet Sauvignon"), ("medoc", "Cabernet Sauvignon"), ("médoc", "Cabernet Sauvignon"),
    ("pessac leognan", "Cabernet Sauvignon"), ("pessac léognan", "Cabernet Sauvignon"),
    ("saint emilion", "Merlot"), ("saint émilion", "Merlot"), ("pomerol", "Merlot"), ("fronsac", "Merlot"),
    ("lalande de pomerol", "Merlot"), ("sauternes", "Sémillon"), ("barsac", "Sémillon"),
    ("tokaji", "Furmint"), ("santorini", "Assyrtiko"), ("naoussa", "Xinomavro"), ("nemea", "Agiorgitiko"),
    ("vinho verde", "Alvarinho"),
]

# Region -> (land, aliaser). Mere specifikke områder (fx Barolo) peger på deres region (Piemonte).
REGIONS = {
    "Bordeaux": ("Frankrig", ["bordeaux", "pauillac", "margaux", "saint julien", "saint estephe", "saint estèphe", "medoc",
                              "médoc", "haut medoc", "haut médoc", "saint emilion", "saint émilion", "pomerol", "fronsac",
                              "pessac leognan", "pessac léognan", "graves", "sauternes", "barsac", "entre deux mers",
                              "lalande de pomerol", "listrac", "moulis", "côtes de bourg", "blaye"]),
    "Bourgogne": ("Frankrig", ["bourgogne", "burgundy", "chablis", "meursault", "puligny montrachet", "chassagne montrachet",
                               "gevrey chambertin", "nuits saint georges", "vosne romanee", "vosne romanée", "chambolle musigny",
                               "pommard", "volnay", "santenay", "mercurey", "givry", "rully", "macon", "mâcon",
                               "pouilly fuisse", "pouilly fuissé", "saint veran", "saint véran", "marsannay",
                               "cote de beaune", "côte de beaune", "cote de nuits", "côte de nuits", "aloxe corton",
                               "savigny les beaune", "beaune", "corton", "irancy", "hautes cotes", "hautes côtes"]),
    "Beaujolais": ("Frankrig", ["beaujolais", "morgon", "fleurie", "moulin a vent", "moulin-à-vent", "brouilly",
                                "julienas", "juliénas", "chiroubles", "saint amour", "regnie", "régnié", "chenas", "chénas"]),
    "Rhône": ("Frankrig", ["rhone", "rhône", "chateauneuf du pape", "châteauneuf du pape", "gigondas", "vacqueyras",
                           "hermitage", "crozes hermitage", "cote rotie", "côte rôtie", "cornas", "saint joseph",
                           "condrieu", "rasteau", "lirac", "tavel", "cairanne", "ventoux", "luberon", "costieres de nimes"]),
    "Loire": ("Frankrig", ["loire", "sancerre", "pouilly fume", "pouilly fumé", "vouvray", "savennieres", "savennières",
                           "chinon", "bourgueil", "saumur", "muscadet", "anjou", "touraine", "menetou salon", "quincy",
                           "montlouis", "reuilly"]),
    "Champagne": ("Frankrig", ["champagne"]),
    "Alsace": ("Frankrig", ["alsace", "elsass"]),
    "Languedoc-Roussillon": ("Frankrig", ["languedoc", "roussillon", "minervois", "corbieres", "corbières", "faugeres",
                                          "faugères", "saint chinian", "pic saint loup", "fitou", "limoux", "pays d oc",
                                          "pays d'oc", "picpoul de pinet", "terrasses du larzac", "collioure", "banyuls", "maury"]),
    "Provence": ("Frankrig", ["provence", "bandol", "cassis", "bellet", "palette"]),
    "Sydvestfrankrig": ("Frankrig", ["cahors", "madiran", "bergerac", "gaillac", "jurancon", "jurançon", "gascogne",
                                     "irouleguy", "irouléguy", "fronton", "marcillac"]),
    "Jura": ("Frankrig", ["jura", "arbois"]),
    "Piemonte": ("Italien", ["piemonte", "piedmont", "barolo", "barbaresco", "langhe", "roero", "gavi", "asti", "alba",
                             "gattinara", "ghemme", "monferrato", "dogliani"]),
    "Toscana": ("Italien", ["toscana", "tuscany", "chianti", "montalcino", "brunello", "montepulciano", "bolgheri",
                            "maremma", "morellino", "carmignano", "san gimignano", "super tuscan"]),
    "Veneto": ("Italien", ["veneto", "amarone", "valpolicella", "ripasso", "soave", "bardolino", "prosecco", "lugana",
                           "valdobbiadene", "conegliano"]),
    "Friuli": ("Italien", ["friuli", "collio", "colli orientali"]),
    "Trentino-Alto Adige": ("Italien", ["alto adige", "südtirol", "sudtirol", "trentino"]),
    "Lombardiet": ("Italien", ["lombardia", "franciacorta", "valtellina", "oltrepo"]),
    "Abruzzo": ("Italien", ["abruzzo", "montepulciano d abruzzo", "montepulciano d'abruzzo"]),
    "Marche": ("Italien", ["marche", "verdicchio", "rosso conero", "conero"]),
    "Umbrien": ("Italien", ["umbria", "montefalco", "orvieto"]),
    "Lazio": ("Italien", ["lazio", "frascati"]),
    "Campania": ("Italien", ["campania", "taurasi", "fiano di avellino", "greco di tufo", "falanghina"]),
    "Puglia": ("Italien", ["puglia", "apulia", "salento", "manduria", "salice salentino"]),
    "Basilicata": ("Italien", ["basilicata", "vulture"]),
    "Sicilien": ("Italien", ["sicilien", "sicilia", "sicily", "etna", "vittoria", "marsala"]),
    "Sardinien": ("Italien", ["sardinien", "sardegna", "sardinia"]),
    "Rioja": ("Spanien", ["rioja"]),
    "Ribera del Duero": ("Spanien", ["ribera del duero"]),
    "Rueda": ("Spanien", ["rueda"]),
    "Toro": ("Spanien", ["toro"]),
    "Priorat": ("Spanien", ["priorat", "priorato", "montsant"]),
    "Rías Baixas": ("Spanien", ["rias baixas", "rías baixas"]),
    "Bierzo": ("Spanien", ["bierzo"]),
    "Navarra": ("Spanien", ["navarra"]),
    "Penedès / Cava": ("Spanien", ["penedes", "penedès", "cava", "corpinnat"]),
    "Jumilla / Murcia": ("Spanien", ["jumilla", "yecla", "bullas", "murcia"]),
    "Jerez": ("Spanien", ["jerez", "sherry", "manzanilla"]),
    "La Mancha / Valencia": ("Spanien", ["la mancha", "valdepenas", "valdepeñas", "valencia", "utiel requena", "alicante"]),
    "Douro": ("Portugal", ["douro", "porto", "port"]),
    "Dão": ("Portugal", ["dao", "dão"]),
    "Alentejo": ("Portugal", ["alentejo"]),
    "Vinho Verde": ("Portugal", ["vinho verde"]),
    "Mosel": ("Tyskland", ["mosel"]),
    "Rheingau": ("Tyskland", ["rheingau"]),
    "Pfalz": ("Tyskland", ["pfalz"]),
    "Rheinhessen": ("Tyskland", ["rheinhessen"]),
    "Nahe": ("Tyskland", ["nahe"]),
    "Baden": ("Tyskland", ["baden"]),
    "Franken": ("Tyskland", ["franken"]),
    "Wachau": ("Østrig", ["wachau"]),
    "Kamptal": ("Østrig", ["kamptal"]),
    "Kremstal": ("Østrig", ["kremstal"]),
    "Burgenland": ("Østrig", ["burgenland"]),
    "Steiermark": ("Østrig", ["steiermark", "styria"]),
    "Napa Valley": ("USA", ["napa"]),
    "Sonoma": ("USA", ["sonoma", "russian river"]),
    "Paso Robles": ("USA", ["paso robles"]),
    "Californien": ("USA", ["californien", "california", "lodi", "central coast", "santa barbara"]),
    "Oregon": ("USA", ["oregon", "willamette"]),
    "Washington": ("USA", ["washington state", "columbia valley"]),
    "Mendoza": ("Argentina", ["mendoza", "uco valley", "lujan de cuyo", "luján de cuyo"]),
    "Salta": ("Argentina", ["salta", "cafayate"]),
    "Patagonien": ("Argentina", ["patagonien", "patagonia"]),
    "Maipo": ("Chile", ["maipo"]),
    "Colchagua": ("Chile", ["colchagua"]),
    "Casablanca": ("Chile", ["casablanca"]),
    "Barossa": ("Australien", ["barossa"]),
    "McLaren Vale": ("Australien", ["mclaren vale"]),
    "Margaret River": ("Australien", ["margaret river"]),
    "Coonawarra": ("Australien", ["coonawarra"]),
    "Yarra Valley": ("Australien", ["yarra"]),
    "Marlborough": ("New Zealand", ["marlborough"]),
    "Central Otago": ("New Zealand", ["central otago"]),
    "Hawke's Bay": ("New Zealand", ["hawkes bay", "hawke s bay"]),
    "Stellenbosch": ("Sydafrika", ["stellenbosch"]),
    "Swartland": ("Sydafrika", ["swartland"]),
    "Tokaj": ("Ungarn", ["tokaj", "tokaji"]),
    "Santorini": ("Grækenland", ["santorini"]),
    "Bekaa": ("Libanon", ["bekaa"]),
    "Kakheti": ("Georgien", ["kakheti"]),
}

# Producenter hvis navn indeholder et andet områdes navn (Concha y Toro er chilensk, ikke fra Toro i Spanien)
PLACE_IN_PRODUCER = ["concha y toro", "bodega norton", "marques de riscal rueda"]

_PCT_RX = re.compile(r"(\d{1,3})\s*%\s*([A-Za-zÀ-ÿ' \-]{3,30})")


def _grape_hits(text: str) -> list[tuple[int, str]]:
    """Alle druer nævnt i teksten som (position, kanonisk navn) – længste synonymer først."""
    t = " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "
    hits, taken = [], []
    pairs = sorted(((fold(s), g) for g, syns in GRAPE_SYNONYMS.items() for s in syns), key=lambda x: -len(x[0]))
    for syn, grape in pairs:
        key = " " + re.sub(r"[^a-z0-9]+", " ", syn).strip() + " "
        start = 0
        while True:
            i = t.find(key, start)
            if i < 0:
                break
            if not any(a <= i < b for a, b in taken):
                hits.append((i, grape))
                taken.append((i, i + len(key)))
            start = i + 1
    return sorted(hits)


RED_GRAPES = {"Cabernet Sauvignon", "Cabernet Franc", "Merlot", "Pinot Noir", "Syrah", "Grenache", "Mourvèdre",
              "Tempranillo", "Sangiovese", "Nebbiolo", "Barbera", "Dolcetto", "Corvina", "Primitivo", "Negroamaro",
              "Nero d'Avola", "Nerello Mascalese", "Aglianico", "Montepulciano", "Malbec", "Carménère", "Pinotage", "Gamay",
              "Cinsault", "Carignan", "Tannat", "Touriga Nacional", "Mencía", "Bobal", "Blaufränkisch", "Zweigelt",
              "Petit Verdot", "Lagrein", "Teroldego", "Xinomavro", "Agiorgitiko", "Saperavi", "Rondo"}
WHITE_GRAPES = set(GRAPE_SYNONYMS) - RED_GRAPES


def grape_fits(grape: str | None, wine_type: str | None, name: str = "") -> bool:
    """Passer druen med vinens farve? (Blanc de Noirs må gerne være af røde druer.)"""
    if not grape or wine_type not in ("rød", "hvid"):
        return True
    if wine_type == "hvid" and grape in RED_GRAPES:
        return bool(re.search(r"blanc\s+de\s+noir|blanc\s+des\s+noir|white\s+zinfandel", fold(name)))
    return not (wine_type == "rød" and grape in WHITE_GRAPES)


def detect_grape(name: str, categories=(), description: str = "", wine_type: str | None = None) -> str | None:
    """Den (dominerende) drue: 1) i navnet, 2) i kategorier/egenskaber, 3) givet af appellationen,
    4) højeste procent i beskrivelsen (fx '85% Merlot'), 5) første drue nævnt tidligt i beskrivelsen.
    Druer der ikke passer med vinens farve springes over (fx Syrah i en hvid Crozes-Hermitage)."""
    ok = lambda g: grape_fits(g, wine_type, name)  # noqa: E731
    for src in (name, " | ".join(categories or [])):
        hits = [g for _, g in _grape_hits(src) if ok(g)]
        if hits:
            return hits[0]
    nm = " " + re.sub(r"[^a-z0-9]+", " ", fold(f"{name} {' '.join(categories or [])}")) + " "
    for app, grape in APPELLATION_GRAPE:
        if " " + re.sub(r"[^a-z0-9]+", " ", fold(app)).strip() + " " in nm and ok(grape):
            return grape
    best = None
    for pct, words in _PCT_RX.findall(description or ""):
        hits = [g for _, g in _grape_hits(words) if ok(g)]
        if hits and (best is None or int(pct) > best[0]):
            best = (int(pct), hits[0])
    if best:
        return best[1]
    hits = [g for _, g in _grape_hits((description or "")[:400]) if ok(g)]
    return hits[0] if hits else None


def detect_region(*texts: str, country: str | None = None) -> tuple[str | None, str | None]:
    """(region, land) ud fra navn, kategorier og beskrivelse – mest specifikke alias vinder.
    country: kun regioner i dette land (bruges når teksten er en beskrivelse)."""
    aliases = sorted(((fold(a), r, c) for r, (c, al) in REGIONS.items() for a in al if not country or c == country),
                     key=lambda x: -len(x[0]))
    for text in texts:
        if not text:
            continue
        t = " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "
        for producer in PLACE_IN_PRODUCER:  # producentnavne der indeholder et stednavn
            t = t.replace(f" {producer} ", " ")
        for alias, region, country in aliases:
            if " " + re.sub(r"[^a-z0-9]+", " ", alias).strip() + " " in t:
                return region, country
    return None, None


WINE_WORDS = ["vin", "rødvin", "roedvin", "hvidvin", "rosévin", "rosevin", "mousserende", "champagne", "cava", "prosecco",
              "crémant", "cremant", "portvin", "sherry", "dessertvin", "orangevin", "wine", "vino", "vinho", "wein",
              "rotwein", "weisswein", "weißwein", "sekt", "spumante", "rosé"]


def clearly_wine(name: str, categories=(), grape: str | None = None) -> bool:
    """Streng kontrol til butikker med andre varer end vin (fx supermarkeder):
    vin-ord i navn/kategori, en kendt drue, eller en kendt vin-appellation i navnet."""
    if has_word(f"{name} | {' | '.join(categories or [])}", WINE_WORDS):
        return True
    if grape or _grape_hits(name):
        return True
    nm = " " + re.sub(r"[^a-z0-9]+", " ", fold(name)) + " "
    return any(" " + re.sub(r"[^a-z0-9]+", " ", fold(app)).strip() + " " in nm for app, _ in APPELLATION_GRAPE)
