"""Tekst-parsing: priser, mængderabat, årgang, flaskestørrelse, vintype, land og anmelderpoint.

Alt her er rene funktioner uden netværk, så de er lette at teste.
"""
from __future__ import annotations

import re
import unicodedata
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
    (re.compile(r"(?<![\d,.])(\d{1,2})\s*\+?\s*(?:stk\.?|fl\.?|flasker|flaske)\s*[:\-–]?\s*(?:á|a|à|kun)?\s*(?:kr\.?\s*)?" + _NUM, re.I), "auto"),
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
    r"(før\s*pris|førpris|normalpris|normal\s+pris|laveste\s+pris\s+(?:de\s+)?(?:sidste|seneste)\s+30\s+dage|før|vejl\.?\s*(?:udsalgs)?pris|vejledende\s+(?:udsalgs)?pris)\s*:?\s*(?:kr\.?\s*)?"
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
        kind = "vejledende" if "vejl" in label else "førpris"
        if best[0] is None or (kind == "førpris" and best[1] != "førpris"):
            best = (v, kind)
    return best


# ---------------------------------------------------------------- årgang og størrelse

def parse_vintage(text: str) -> int | None:
    if not text:
        return None
    for m in re.finditer(r"(?<![\d,.])(19[4-9]\d|20[0-3]\d)(?![\d,.]|\s*(?:ml|cl|år|years))", text):
        y = int(m.group(1))
        if 1945 <= y <= 2035:
            return y
    return None


def parse_volume_cl(text: str) -> float | None:
    if not text:
        return None
    t = text.lower()
    if "magnum" in t:
        return 150.0
    if re.search(r"\bjeroboam\b|\bdouble magnum\b", t):
        return 300.0
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
    return None


# ---------------------------------------------------------------- normalisering

def fold(s: str) -> str:
    """Små bogstaver, uden accenter (æøå bevares som ae/oe/aa)."""
    s = s.lower().replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def has_word(text: str, words: Iterable[str]) -> str | None:
    t = " " + re.sub(r"[^a-z0-9]+", " ", fold(text)) + " "
    for w in words:
        if " " + fold(w) + " " in t:
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
    "glas", "vinglas", "karaffel", "dekanter", "proptraekker", "proptrækker", "vinkoeler", "vinkøler", "vinreol",
    "vinkoeleskab", "vinkøleskab", "gavekort", "gaveaeske", "gaveæske", "gavekurv", "gavepose", "smagning",
    "vinsmagning", "arrangement", "billet", "kursus", "bog", "fragt", "pant", "trae kasse", "trækasse",
    "smagekasse", "vinpakke", "mixkasse", "blandet kasse", "abonnement", "vinklub", "alkoholfri", "alcohol free",
    "0,0%", "alkoholfrit",
]

# ---------------------------------------------------------------- vintype

TYPES = ["rød", "hvid", "rosé", "mousserende", "dessert", "orange"]

_EXPLICIT = [
    ("mousserende", ["mousserende", "sparkling", "champagne", "cava", "prosecco", "cremant", "crémant", "franciacorta",
                     "spumante", "sekt", "pet nat", "pét nat", "petnat", "pet-nat", "frizzante", "lambrusco", "boblevin", "bobler"]),
    ("dessert", ["portvin", "port", "sherry", "madeira", "marsala", "sauternes", "tokaji", "tokaj", "dessertvin", "hedvin",
                 "vin santo", "icewine", "ice wine", "eiswein", "banyuls", "maury", "pedro ximenez", "beerenauslese",
                 "trockenbeerenauslese", "vin doux naturel", "passito", "recioto", "late harvest", "moscatel de setubal", "barsac"]),
    ("orange", ["orangevin", "orange wine", "orange vin", "skin contact", "skinkontakt", "ramato", "amber wine"]),
    ("rosé", ["rosé", "rose", "rosevin", "rosévin", "rosato", "rosado", "blush", "clairet"]),
    ("hvid", ["hvidvin", "hvid", "white wine", "white", "blanc", "bianco", "blanco", "weisswein", "weiss", "branco"]),
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
    "Chile": ["chile", "maipo", "colchagua", "casablanca", "maule", "aconcagua", "rapel"],
    "Argentina": ["argentina", "mendoza", "salta", "uco valley", "patagonien", "patagonia"],
    "Australien": ["australien", "australia", "barossa", "mclaren vale", "margaret river", "coonawarra", "yarra", "clare valley"],
    "New Zealand": ["new zealand", "newzealand", "marlborough", "central otago", "hawkes bay", "hawke s bay"],
    "Sydafrika": ["sydafrika", "south africa", "stellenbosch", "swartland", "western cape", "paarl", "franschhoek"],
    "Ungarn": ["ungarn", "hungary", "tokaj", "tokaji", "villany"],
    "Grækenland": ["graekenland", "grækenland", "greece", "santorini", "nemea", "naoussa"],
    "Danmark": ["danmark", "denmark", "dansk vin", "dansk"],
    "Libanon": ["libanon", "lebanon", "bekaa"],
    "Georgien": ["georgien", "georgia", "kakheti"],
    "Slovenien": ["slovenien", "slovenia"],
    "Kroatien": ["kroatien", "croatia"],
    "Uruguay": ["uruguay"],
    "Israel": ["israel"],
    "England": ["england", "english sparkling"],
    "Schweiz": ["schweiz", "switzerland"],
    "Moldova": ["moldova"],
    "Rumænien": ["rumaenien", "rumænien", "romania"],
}


def classify_type(name: str, categories: Iterable[str] = (), description: str = "") -> str | None:
    """Vintype ud fra kategorier/tags først, derefter navn, derefter druer/områder."""
    cats = " | ".join(categories or [])
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


def detect_country(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        for country, words in COUNTRIES.items():
            if has_word(text, words):
                return country
    return None


def is_wine(name: str, categories: Iterable[str] = (), wine_type: str | None = None) -> bool:
    cats = " ".join(categories or [])
    if has_word(name, NON_WINE):
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
    return mapping.get(low, n)


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
