"""Generisk webshop: find produktsider via sitemap.xml og læs schema.org Product (JSON-LD/microdata).

Dækker bl.a. Magento, DanDomain/SmartWeb, Shoporama, PrestaShop og egenudviklede shops.
Hver kørsel henter højst `max_pages` sider pr. forhandler; resten roteres ind i de næste kørsler,
så Pi'en (og forhandlerens server) ikke belastes.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterator
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import extract as X
from ..http import Blocked
from ..models import Product

log = logging.getLogger(__name__)

EXCLUDE = re.compile(
    r"/(blog|nyheder|news|artikel|artikler|kategori|category|categories|collections?|side|pages?|om-?os|about|kontakt|contact|"
    r"handelsbetingelser|cookie|privatliv|privacy|login|konto|account|kurv|cart|checkout|search|soeg|søg|tag|brand|producent(?:er)?/?$|"
    r"events?|arrangementer|smagninger|gavekort|wp-content|feed)(/|$|\?)", re.I)


def _locs(xml: str) -> list[tuple[str, str | None]]:
    out = []
    for block in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, flags=re.S | re.I):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, flags=re.S | re.I)
        if not loc:
            continue
        lm = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, flags=re.S | re.I)
        out.append((loc.group(1).replace("&amp;", "&"), lm.group(1) if lm else None))
    return out


def sitemap_urls(client, base: str, limit_children: int = 40) -> list[str]:
    candidates = client.sitemaps(base) or [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/sitemap-index.xml"]
    for sm in candidates:
        try:
            xml = client.get_text(sm)
        except (Blocked, Exception) as e:  # noqa: BLE001
            log.debug("sitemap %s: %s", sm, e)
            continue
        if not xml or "<loc>" not in xml.lower():
            continue
        if "<sitemapindex" in xml.lower():
            children = [u for u, _ in _locs(xml)]
            prod = [c for c in children if re.search(r"produ[ck]t|product|vare|wine|vin", c, re.I)]
            urls: list[str] = []
            for child in (prod or children)[:limit_children]:
                try:
                    cx = client.get_text(child)
                except Exception:  # noqa: BLE001
                    continue
                if cx:
                    urls += [u for u, _ in _locs(cx)]
            if urls:
                return urls
        else:
            return [u for u, _ in _locs(xml)]
    return []


def detect(client, base: str) -> bool:
    return bool(sitemap_urls(client, base, limit_children=2))


# ------------------------------------------------------------ produktside

def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _is_type(node: dict, t: str) -> bool:
    typ = node.get("@type")
    return t in typ if isinstance(typ, list) else typ == t


def _jsonld_products(soup: BeautifulSoup) -> list[dict]:
    out = []
    for s in soup.find_all("script", type=re.compile("ld\\+json", re.I)):
        raw = s.string or s.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except ValueError:
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", raw.strip()))
            except ValueError:
                continue
        out += [n for n in _walk(data) if _is_type(n, "Product") or _is_type(n, "ProductGroup")]
    return out


def _offer_info(offers) -> tuple[float | None, bool, list[tuple[int, float]]]:
    price, in_stock, tiers = None, True, []
    for o in [offers] if isinstance(offers, dict) else (offers or []):
        if not isinstance(o, dict):
            continue
        cur = (o.get("priceCurrency") or "DKK").upper()
        if cur not in ("DKK", "KR", ""):
            continue
        p = X.parse_price(o.get("price") or o.get("lowPrice"))
        specs = o.get("priceSpecification") or []
        for sp in [specs] if isinstance(specs, dict) else specs:
            if not isinstance(sp, dict):
                continue
            sp_p = X.parse_price(sp.get("price"))
            eq = sp.get("eligibleQuantity") or {}
            q = eq.get("minValue") if isinstance(eq, dict) else None
            if sp_p and q and int(float(q)) > 1:
                tiers.append((int(float(q)), sp_p))
            elif sp_p and not p:
                p = sp_p
        if p and (price is None or p < price):
            price = p
        av = str(o.get("availability") or "")
        if av and not re.search(r"InStock|PreOrder|LimitedAvailability|OnlineOnly", av):
            in_stock = False
    return price, in_stock, tiers


_RELATED = re.compile(r"related|recommend|upsell|cross-?sell|similar|anbefal|andre-?kunder|also-?bought|carousel|slider|footer|mega-?menu|recent", re.I)


def _in_related(el) -> bool:
    for parent in el.parents:
        if parent.name in ("footer", "aside", "nav"):
            return True
        cls = " ".join(parent.get("class") or []) + " " + (parent.get("id") or "")
        if cls.strip() and _RELATED.search(cls):
            return True
    return False


_OLD_CLASS = re.compile(r"old|before|compare|regular|was|strike|line-through|foer|for-pris|normal", re.I)


def parse_product_page(html: str, url: str, retailer_id: str) -> Product | None:
    soup = BeautifulSoup(html, "lxml") if _has_lxml() else BeautifulSoup(html, "html.parser")
    nodes = _jsonld_products(soup)
    name = price = None
    in_stock, tiers, producer, image, gtin, desc, cats = True, [], None, None, None, "", []
    if nodes:
        n = nodes[0]
        name = n.get("name")
        price, in_stock, tiers = _offer_info(n.get("offers"))
        if price is None:  # ProductGroup -> se på varianter
            for v in n.get("hasVariant") or []:
                p, s, t = _offer_info(v.get("offers"))
                if p and (price is None or p < price):
                    price, in_stock, tiers = p, s, t
        b = n.get("brand")
        producer = (b.get("name") if isinstance(b, dict) else b) or None
        img = n.get("image")
        image = img[0] if isinstance(img, list) and img else (img.get("url") if isinstance(img, dict) else img)
        gtin = n.get("gtin13") or n.get("gtin") or n.get("gtin14")
        desc = X.strip_html(n.get("description") or "")
        c = n.get("category")
        cats = [c] if isinstance(c, str) else (c or [])
    # Fallback: microdata / Open Graph
    if price is None:
        meta = soup.find("meta", property="product:price:amount") or soup.find(attrs={"itemprop": "price"})
        if meta:
            price = X.parse_price(meta.get("content") or meta.get_text())
    if not name:
        og = soup.find("meta", property="og:title")
        h1 = soup.find("h1")
        name = (og.get("content") if og else None) or (h1.get_text(" ", strip=True) if h1 else None)
    if not name or not price:
        return None

    # Tekst omkring produktet (fra <h1> og frem) – undgår priser fra "relaterede produkter".
    h1 = soup.find("h1")
    window = ""
    if h1:
        parts, size = [], 0
        for el in h1.find_all_next(string=True):
            if el.parent and el.parent.name in ("script", "style"):
                continue
            if _in_related(el):
                break
            t = el.strip()
            if t:
                parts.append(t)
                size += len(t)
                if size > 1500:
                    break
        window = " ".join(parts)
    before, kind = X.parse_before_price(window, price)
    if not before and h1:
        for el in h1.find_all_next(["del", "s", "span", "div"], limit=250):
            cls = " ".join(el.get("class") or [])
            if _in_related(el):
                break
            if el.name in ("del", "s") or _OLD_CLASS.search(cls):
                v = X.parse_price(el.get_text(" ", strip=True))
                if v and price < v <= price * 2.5:
                    before, kind = v, "førpris"
                    break
    if not tiers:
        tiers = X.parse_quantity_prices(window, price)
    crumbs = [a.get_text(" ", strip=True) for a in soup.select("nav.breadcrumb a, .breadcrumb a, .breadcrumbs a, [itemtype*=BreadcrumbList] [itemprop=name]")][:8]
    return Product(
        retailer_id=retailer_id, url=url, name=name.strip(), price=price, before_price=before, before_kind=kind,
        producer=producer, categories=[c for c in cats + crumbs if c], description=(desc + "\n" + window)[:6000],
        in_stock=in_stock, image=urljoin(url, image) if isinstance(image, str) else None, qty_tiers=tiers,
        gtin=str(gtin) if gtin else None,
    )


_LXML = None


def _has_lxml() -> bool:
    global _LXML
    if _LXML is None:
        try:
            import lxml  # noqa: F401
            _LXML = True
        except ImportError:
            _LXML = False
    return _LXML


OFFER_RX = re.compile(
    r"tilbud|udsalg|kampagne|nedsat|spar-|/spar\b|outlet|sale\b|/sale|black-?friday|black-?week|cyber|restsalg|"
    r"lagersalg|ugens|maanedens|månedens|deals?\b|offers?\b|prisfald|skarpe-?priser|bundpris|kassetilbud|spotpris|nedsatte|prisbombe|super-?pris", re.I)
OFFER_SKIP = re.compile(r"betingelser|vilkaar|vilkår|nyhedsbrev|newsletter|gavekort|login|konto|kurv|cart|blog|cookie", re.I)
PAGINATION_RX = re.compile(r"[?&](?:p|page|side|pg|sida)=(\d+)|/(?:page|side)[/-](\d+)", re.I)


def _soup_links(html: str, page_url: str, host: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"]).split("#")[0]
        if urlparse(u).netloc.replace("www.", "") == host.replace("www.", ""):
            out.append((u, a.get_text(" ", strip=True)[:80]))
    return out


# Ligner en produktside – ikke en tilbudsoversigt
PRODUCT_LIKE = re.compile(r"/products?/|/produkt/|/varekort/|/vare/|-\d+p\.html$|/p/\d|\.aspx\?id=|/item/", re.I)
# Blandede kasser, gaver og ikke-vin – kan ikke sammenlignes med andre forhandleres priser
OFFER_PENALTY = re.compile(r"smagekasse|pakke|vingave|gave|spiritus|whisky|gin|\bøl\b|oel|chokolade|glas|tilbehoer|tilbehør", re.I)
COMMON_OFFER_PATHS = ["/tilbud", "/tilbud/", "/alle-tilbud", "/vintilbud", "/udsalg", "/kampagner", "/kampagne",
                      "/vin/tilbud", "/shop/tilbud", "/tilbud.html", "/sale", "/collections/tilbud", "/produkt-kategori/tilbud/"]


def _rank_offer(u: str, text: str) -> int:
    path = urlparse(u).path
    if PRODUCT_LIKE.search(path) or OFFER_SKIP.search(path + " " + text) or path in ("", "/"):
        return 0
    score = (2 if OFFER_RX.search(text) else 0) + (1 if OFFER_RX.search(path) else 0)
    if not score:
        return 0
    if OFFER_PENALTY.search(path + " " + text):
        score -= 2
    if path.strip("/").count("/") == 0:  # topniveau som /tilbud eller /alle-tilbud
        score += 1
    return score


def discover_offer_pages(client, base: str, host: str, limit: int = 6, sitemap_urls_: list[str] | None = None) -> list[str]:
    """Find tilbudssider: 1) links på forsiden, 2) tilbuds-URL'er i sitemap, 3) almindelige adresser som /tilbud."""
    scored: dict[str, int] = {}
    try:
        html = client.get_text(base + "/")
    except Exception:  # noqa: BLE001
        html = None
    for u, text in (_soup_links(html, base + "/", host) if html else []):
        s = _rank_offer(u, text)
        if s > 0:
            scored[u] = max(scored.get(u, 0), s)
    for u in sitemap_urls_ or []:
        s = _rank_offer(u, "")
        if s > 0:
            scored.setdefault(u, s)
    if not scored:
        for p in COMMON_OFFER_PATHS:
            u = base + p
            try:
                r = client.get(u)
            except Exception:  # noqa: BLE001
                continue
            final = urlparse(r.url or u).path.rstrip("/")
            if r.status_code == 200 and final not in ("",) and "html" in r.headers.get("Content-Type", "html"):
                scored[r.url or u] = 1
                break
    # spring under-sider over, når oversigten selv er med (fx /tilbud/hvidvin når /tilbud findes)
    ranked = [u for u, _ in sorted(scored.items(), key=lambda x: (-x[1], len(x[0])))]
    norm = lambda u: urlparse(u).path.rstrip("/").lower()  # noqa: E731
    chosen: list[str] = []
    for u in ranked:
        if any(norm(u).startswith(norm(c) + "/") for c in chosen) or norm(u) in {norm(c) for c in chosen}:
            continue
        chosen.append(u)
    return chosen[:limit]


def crawl_offer_pages(client, pages: list[str], host: str, is_product, max_listing_pages: int = 15) -> list[str]:
    """Henter tilbudssider inkl. 'side 2, 3 ...' og returnerer de produkt-URL'er, de linker til."""
    queue, seen, products = list(dict.fromkeys(pages)), set(), []
    while queue and len(seen) < max_listing_pages:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        try:
            html = client.get_text(page)
        except Exception:  # noqa: BLE001
            continue
        if not html:
            continue
        root = urlparse(page).path.rstrip("/")
        for u, _ in _soup_links(html, page, host):
            pu = urlparse(u)
            if PAGINATION_RX.search(u) and pu.path.rstrip("/").startswith(root) and u not in seen and len(queue) < 60:
                queue.append(u)
            elif is_product(u):
                products.append(u)
    return list(dict.fromkeys(products))


def scan(client, retailer: dict, max_products: int = 400, db=None, background: int | None = None,
         info: dict | None = None) -> Iterator[Product]:
    """Henter først alle produkter fra tilbudssiderne, derefter op til `background` øvrige produktsider
    (bruges som markedspris for de andre forhandleres tilbud)."""
    info = info if info is not None else {}
    base = retailer["url"].rstrip("/")
    host = urlparse(base).netloc
    include = re.compile(retailer["product_url_pattern"], re.I) if retailer.get("product_url_pattern") else None
    all_urls = [u for u in sitemap_urls(client, base) if urlparse(u).netloc.endswith(host.replace("www.", ""))]
    urls = [u for u in all_urls if (include.search(u) if include else not EXCLUDE.search(urlparse(u).path))]
    urls = [u for u in urls if urlparse(u).path not in ("", "/") and
            (not OFFER_RX.search(urlparse(u).path) or PRODUCT_LIKE.search(urlparse(u).path))]
    known = set(urls)

    def is_product(u: str) -> bool:
        if include:
            return bool(include.search(u))
        if known:
            return u in known
        return not EXCLUDE.search(urlparse(u).path) and not OFFER_RX.search(urlparse(u).path) and urlparse(u).path.count("/") >= 1

    offer_pages = [urljoin(base + "/", o) for o in retailer.get("offer_urls") or []]
    if not offer_pages or retailer.get("discover_offers", True):
        offer_pages += discover_offer_pages(client, base, host, sitemap_urls_=all_urls)
    offer_pages = list(dict.fromkeys(offer_pages))
    offer_products = crawl_offer_pages(client, offer_pages, host, is_product) if offer_pages else []
    info.update(offer_pages=offer_pages, offer_products=len(offer_products))
    log.info("%s: %d tilbudssider, %d tilbudsprodukter, %d produkt-URL'er i sitemap",
             retailer["id"], len(offer_pages), len(offer_products), len(urls))

    offer_set = set(offer_products)
    # Er forsiden selv "tilbudssiden", står der også almindelige varer – kræv da en førpris
    weak = (all(urlparse(p).path in ("", "/") for p in offer_pages) if offer_pages else False) \
        and not retailer.get("offer_all_listed")
    rest = db.page_order(retailer["id"], [], [u for u in urls if u not in offer_set]) if db else [u for u in urls if u not in offer_set]
    bg = max_products if background is None else min(background, max_products)
    order = offer_products[:max_products] + rest[:max(0, min(bg, max_products - len(offer_products)))]
    for u in order:
        try:
            r = client.get(u)
        except Blocked:
            if db:
                db.mark_page(u, retailer["id"], False, "robots")
            continue
        except Exception as e:  # noqa: BLE001
            log.debug("%s: %s", u, e)
            if db:
                db.mark_page(u, retailer["id"], None, "error")
            continue
        if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "html"):
            if db:
                db.mark_page(u, retailer["id"], None, str(r.status_code))
            continue
        p = parse_product_page(r.text, r.url or u, retailer["id"])
        if db:
            db.mark_page(u, retailer["id"], p is not None, "ok")
        if p:
            p.on_offer = bool(p.before_price) or (u in offer_set and (not weak or X.has_offer_tag(p.categories)))
            yield p
