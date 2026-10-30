"""Tre falske webshops (Shopify, WooCommerce og generisk sitemap/JSON-LD) til ende-til-ende-test.

    python tests/fake_shops.py   # starter på port 8701-8703
"""
from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

random.seed(7)

WINES = [  # producent, navn, type, land, årgang, normalpris
    ("Fontanafredda", "Barolo DOCG", "rød", "Italien", 2019, 299),
    ("Castello di Ama", "Chianti Classico", "rød", "Italien", 2021, 229),
    ("Muga", "Rioja Reserva", "rød", "Spanien", 2019, 179),
    ("Cloudy Bay", "Sauvignon Blanc", "hvid", "New Zealand", 2023, 249),
    ("Dr. Loosen", "Riesling Kabinett", "hvid", "Tyskland", 2022, 149),
    ("Château Musar", "Rouge", "rød", "Libanon", 2016, 349),
    ("Whispering Angel", "Côtes de Provence Rosé", "rosé", "Frankrig", 2023, 189),
    ("Bollinger", "Special Cuvée Brut Champagne", "mousserende", "Frankrig", None, 499),
    ("Graham's", "10 Year Old Tawny Port", "dessert", "Portugal", None, 279),
    ("Masi", "Costasera Amarone della Valpolicella Classico", "rød", "Italien", 2018, 329),
    ("Tenuta San Guido", "Guidalberto", "rød", "Italien", 2021, 399),
    ("Domaine Laroche", "Chablis Saint Martin", "hvid", "Frankrig", 2022, 219),
    ("Catena", "Malbec", "rød", "Argentina", 2021, 129),
    ("Torres", "Sangre de Toro", "rød", "Spanien", 2022, 89),
    ("Villa Maria", "Private Bin Pinot Noir", "rød", "New Zealand", 2022, 159),
    ("E. Guigal", "Côtes du Rhône Rouge", "rød", "Frankrig", 2020, 139),
    ("Pazo de Señorans", "Albariño", "hvid", "Spanien", 2023, 199),
    ("Gramona", "Imperial Brut Gran Reserva Cava", "mousserende", "Spanien", 2018, 219),
    ("Penfolds", "Bin 389 Cabernet Shiraz", "rød", "Australien", 2020, 549),
    ("Planeta", "Cerasuolo di Vittoria", "rød", "Italien", 2021, 189),
    ("Tement", "Sauvignon Blanc Grassnitzberg", "hvid", "Østrig", 2021, 299),
    ("Bodegas Alto Moncayo", "Garnacha", "rød", "Spanien", 2019, 269),
    ("Hugel", "Gewurztraminer Classic", "hvid", "Frankrig", 2021, 169),
    ("Quinta do Crasto", "Douro Red", "rød", "Portugal", 2021, 149),
]
CRITICS = {"Barolo DOCG": "94 point James Suckling", "Rioja Reserva": "93 point Robert Parker",
           "Rouge": "95 point Decanter", "Garnacha": "92 point Robert Parker. 5 stjerner i Politiken",
           "Douro Red": "91 point Wine Spectator", "Malbec": "90 point James Suckling",
           "Albariño": "6 stjerner i Berlingske", "Cerasuolo di Vittoria": "Falstaff 91",
           "Sauvignon Blanc Grassnitzberg": "96 point Falstaff", "Chablis Saint Martin": "Jancis Robinson: 92 point"}


def _shop_prices(shop: int):
    out = []
    for i, (prod, name, t, land, y, base) in enumerate(WINES):
        if (i + shop) % 4 == 0:  # ikke alle shops har alle vine
            continue
        f = random.uniform(0.92, 1.08)
        price = round(base * f) - 0.05
        before = None
        if random.random() < 0.3:
            before = price
            price = round(price * random.uniform(0.62, 0.85)) - 0.05
        out.append(dict(prod=prod, name=name, t=t, land=land, y=y, price=price, before=before, i=i))
    return out


class Shopify(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/collections/tilbud/products.json":
            page = int(parse_qs(u.query).get("page", ["1"])[0])
            return self._json({"products": [{"handle": "w2"}, {"handle": "w5"}] if page == 1 else []})
        if u.path == "/products.json":
            page = int(parse_qs(u.query).get("page", ["1"])[0])
            items = _shop_prices(1) if page == 1 else []
            prods = []
            for w in items:
                title = f"{w['name']} {w['y'] or ''}".strip()
                variants = [{"title": "Default Title", "price": f"{w['price']:.2f}",
                             "compare_at_price": f"{w['before']:.2f}" if w["before"] else None, "available": True, "sku": str(w["i"])}]
                if w["i"] % 3 == 0:
                    variants = [{"title": "1 flaske", "price": f"{w['price']:.2f}", "compare_at_price": None, "available": True},
                                {"title": "6 flasker", "price": f"{w['price'] * 6 * 0.85:.2f}", "available": True}]
                prods.append({"title": title, "handle": f"w{w['i']}", "vendor": w["prod"], "product_type":
                              {"rød": "Rødvin", "hvid": "Hvidvin", "rosé": "Rosévin", "mousserende": "Champagne & Mousserende", "dessert": "Portvin"}[w["t"]],
                              "tags": [w["land"]], "body_html": f"<p>{CRITICS.get(w['name'], '')}</p>", "variants": variants,
                              "images": [{"src": f"https://example.com/{w['i']}.jpg"}]})
            prods.append({"title": "Hendrick's Gin 70 cl", "handle": "gin", "vendor": "Hendrick's", "product_type": "Gin",
                          "tags": [], "body_html": "", "variants": [{"title": "Default Title", "price": "299.00", "available": True}]})
            return self._json({"products": prods})
        self.send_response(404); self.end_headers()

    def _json(self, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)


class Woo(Shopify):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/products.json":
            self.send_response(404); self.end_headers(); return
        if u.path == "/wp-json/wc/store/v1/products":
            page = int(parse_qs(u.query).get("page", ["1"])[0])
            items = _shop_prices(2) if page == 1 else []
            out = []
            for w in items:
                reg = w["before"] or w["price"]
                out.append({"name": f"{w['prod']} {w['name']} {w['y'] or ''} 75 cl", "permalink": f"http://127.0.0.1:8702/vin/w{w['i']}",
                            "prices": {"price": str(int(w["price"] * 100)), "regular_price": str(int(reg * 100)),
                                       "currency_minor_unit": 2},
                            "categories": [{"name": "Vin"}], "tags": [], "is_in_stock": True,
                            "description": f"<p>Ved køb af 6 stk.: {w['price'] * 0.9:.2f} kr. pr. stk.</p><p>{CRITICS.get(w['name'], '')}</p>",
                            "short_description": "", "attributes": [{"name": "Land", "terms": [{"name": w["land"]}]}],
                            "images": []})
            return self._json(out, {"X-WP-TotalPages": "1"})
        self.send_response(404); self.end_headers()


class Generic(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        u = urlparse(self.path)
        base = "http://127.0.0.1:8703"
        items = {f"/produkt/w{w['i']}": w for w in _GENERIC}
        if u.path == "/robots.txt":
            return self._send(f"User-agent: *\nDisallow: /kurv\nSitemap: {base}/sitemap.xml\n", "text/plain")
        if u.path == "/sitemap.xml":
            locs = "".join(f"<url><loc>{base}{p}</loc></url>" for p in items) + f"<url><loc>{base}/om-os</loc></url>"
            return self._send(f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>', "application/xml")
        if u.path in items:
            w = items[u.path]
            ld = {"@context": "https://schema.org", "@type": "Product", "name": f"{w['prod']} {w['name']} {w['y'] or ''}",
                  "brand": {"@type": "Brand", "name": w["prod"]}, "category": "Vin > " + w["t"],
                  "offers": {"@type": "Offer", "price": f"{w['price']:.2f}", "priceCurrency": "DKK",
                             "availability": "https://schema.org/InStock"}}
            before = f"<p>Førpris: {w['before']:.2f} kr.</p>".replace(".", ",") if w["before"] else ""
            html = (f"<html><head><script type='application/ld+json'>{json.dumps(ld)}</script></head><body>"
                    f"<nav class='breadcrumb'><a>Vin</a><a>{w['land']}</a></nav><h1>{ld['name']}</h1>"
                    f"<p class='price'>{w['price']:.2f} kr.</p>{before}<p>6 fl. for {w['price'] * 6 * 0.88:.0f} kr</p>"
                    f"<p>{CRITICS.get(w['name'], '')}</p><div class='related'><p>Før 999 kr</p></div></body></html>")
            return self._send(html, "text/html")
        if u.path == "/":
            return self._send("<html><body><nav><a href='/tilbud'>Ugens tilbud</a><a href='/om-os'>Om os</a>"
                              "<a href='/handelsbetingelser'>Betingelser</a></nav></body></html>", "text/html")
        if u.path == "/tilbud":
            offers = [p for p, w in items.items() if w["before"]]
            page = int(parse_qs(u.query).get("page", ["1"])[0])
            half = offers[:2] if page == 1 else offers[2:]
            links = "".join(f"<a href='{p}'>vin</a>" for p in half)
            nxt = "<a href='/tilbud?page=2'>Næste</a>" if page == 1 else ""
            return self._send(f"<html><body><h1>Tilbud</h1>{links}{nxt}</body></html>", "text/html")
        if u.path == "/om-os":
            return self._send("<html><h1>Om os</h1></html>", "text/html")
        self.send_response(404); self.end_headers()

    def _send(self, s, ct):
        b = s.encode()
        self.send_response(200); self.send_header("Content-Type", ct + "; charset=utf-8"); self.end_headers(); self.wfile.write(b)


_GENERIC = _shop_prices(3)


def start():
    servers = []
    for port, h in ((8701, Shopify), (8702, Woo), (8703, Generic)):
        s = ThreadingHTTPServer(("127.0.0.1", port), h)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servers.append(s)
    return servers


if __name__ == "__main__":
    start()
    print("Falske shops kører på 8701-8703. Ctrl+C for at stoppe.")
    threading.Event().wait()
