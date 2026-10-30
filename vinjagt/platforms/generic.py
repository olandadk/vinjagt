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


def _links_from(client, page_url: str, host: str) -> list[str]:
    try:
        html = client.get_text(page_url)
    except Exception:  # noqa: BLE001
        return []
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        u = urljoin(page_url, a["href"]).split("#")[0]
        if urlparse(u).netloc == host and not EXCLUDE.search(urlparse(u).path):
            out.append(u)
    return list(dict.fromkeys(out))


def scan(client, retailer: dict, max_products: int = 400, db=None) -> Iterator[Product]:
    base = retailer["url"].rstrip("/")
    host = urlparse(base).netloc
    include = re.compile(retailer["product_url_pattern"], re.I) if retailer.get("product_url_pattern") else None
    urls = [u for u in sitemap_urls(client, base) if urlparse(u).netloc.endswith(host.replace("www.", ""))]
    urls = [u for u in urls if (include.search(u) if include else not EXCLUDE.search(urlparse(u).path))]
    urls = [u for u in urls if urlparse(u).path not in ("", "/")]

    # Tilbudssider først: produkter der linkes til fra forhandlerens tilbudsside(r)
    priority: list[str] = []
    for off in retailer.get("offer_urls") or []:
        links = _links_from(client, urljoin(base + "/", off), host)
        priority += [u for u in links if (include.search(u) if include else True)]
    known = set(urls)
    priority = [u for u in priority if u in known or include] if known else priority

    order = db.page_order(retailer["id"], priority, urls) if db else list(dict.fromkeys(priority + urls))
    log.info("%s: %d produkt-URL'er i sitemap, henter op til %d", retailer["id"], len(urls), max_products)
    fetched = 0
    for u in order:
        if fetched >= max_products:
            break
        fetched += 1
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
            yield p
