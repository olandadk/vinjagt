"""Ende-til-ende: scan tre falske shops, byg siden og tjek resultatet."""
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import yaml

sys.path.insert(0, os.path.dirname(__file__))
import fake_shops  # noqa: E402

from vinjagt import cli  # noqa: E402


def test_end_to_end(tmp_path):
    servers = fake_shops.start()
    try:
        (tmp_path / "retailers.yaml").write_text(yaml.safe_dump([
            {"name": "Shop Shopify", "url": "http://127.0.0.1:8701"},
            {"name": "Shop Woo", "url": "http://127.0.0.1:8702", "offer_urls": ["/produkt-kategori/aktuelle-tilbud/"]},
            {"name": "Shop Generisk", "url": "http://127.0.0.1:8703"},
            {"name": "Shop Uden Sitemap", "url": "http://127.0.0.1:8704"},
        ]))
        cfg = {"data_dir": str(tmp_path / "data"), "site_dir": str(tmp_path / "site"), "request_delay": 0,
               "retailers_file": str(tmp_path / "retailers.yaml"),
               "ratings": {"csv_dir": str(tmp_path / "ratings")}}
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
        os.makedirs(tmp_path / "ratings")
        (tmp_path / "ratings" / "test.csv").write_text(
            "source;name;producer;vintage;rating;scale\nPolitiken;Chianti Classico;Castello di Ama;2021;6;6\n")

        cli.main(["scan", "--config", str(tmp_path / "config.yaml")])
        # læg 20 dages kunstig prishistorik ind, så historik-referencen også testes
        db = sqlite3.connect(tmp_path / "data" / "vinjagt.sqlite")
        for wid, p in db.execute("SELECT id, price_1 FROM wines").fetchall():
            for d in range(1, 21):
                day = (date.today() - timedelta(days=d)).isoformat()
                db.execute("INSERT OR IGNORE INTO price_history VALUES(?,?,?,?,NULL)", (wid, day, p * 1.25, p * 1.2))
        db.commit()
        status = {r[0]: (r[1], r[2]) for r in db.execute("SELECT id, platform, wines FROM retailers")}
        assert status["shop-shopify"][0] == "shopify"
        assert status["shop-woo"][0] == "woocommerce"
        assert status["shop-generisk"][0] == "generic"
        assert status["shop-uden-sitemap"][0] == "generic"
        n_offer = sum(1 for w in fake_shops._GENERIC if w["before"])
        assert status["shop-uden-sitemap"][1] == n_offer, status  # kun tilbudsprodukterne
        assert all(n > 10 for k, (_, n) in status.items() if k != "shop-uden-sitemap"), status
        offers = db.execute("SELECT offer_products, offer_pages FROM retailers WHERE id='shop-generisk'").fetchone()
        n_before = sum(1 for w in fake_shops._GENERIC if w["before"])
        assert offers[0] == n_before and "/tilbud" in offers[1], offers
        pages = dict(db.execute("SELECT url, is_product FROM pages WHERE retailer_id='shop-generisk'").fetchall())
        assert pages.get("http://127.0.0.1:8703/vin/roedvin") in (None, 0)
        assert db.execute("SELECT on_offer FROM wines WHERE url LIKE '%8701/products/w5'").fetchone()[0] == 1
        assert "/collections/tilbud" in db.execute("SELECT offer_pages FROM retailers WHERE id='shop-shopify'").fetchone()[0]
        assert db.execute("SELECT on_offer FROM wines WHERE url LIKE '%8702/vin/w5'").fetchone()[0] == 1
        gin = db.execute("SELECT COUNT(*) FROM wines WHERE name LIKE '%Gin%'").fetchone()[0]
        assert gin == 0

        cli.main(["run", "--config", str(tmp_path / "config.yaml"), "--only", "ingen"])  # ratings + build + (publish slået fra)
        site = tmp_path / "site"
        deals = json.loads((site / "data" / "deals.json").read_text())
        shops = json.loads((site / "data" / "retailers.json").read_text())
        assert (site / "index.html").exists() and (site / "forhandlere.html").exists()
        assert len(shops) == 4
        assert len(deals) > 10
        d = next(x for x in deals if "Barolo" in x["n"])
        assert d["mn"] >= 1 and d["m1"]  # matchet på tværs af shops
        assert any(r["s"] == "James Suckling" for r in d["rt"])
        assert any(r["s"] == "Politiken" for x in deals if "Chianti" in x["n"] for r in x["rt"])
        assert all(x["p6"] <= x["p1"] for x in deals)
        assert any(x["p6"] < x["p1"] for x in deals if x["r"] == "shop-woo")
        assert any(x["p6"] < x["p1"] for x in deals if x["r"] == "shop-generisk")
        assert any(x["of"] for x in deals) and not all(x["of"] for x in deals)
        # "Før 999" fra relaterede produkter må ikke blive læst som førpris
        assert not any(x["bp"] == 999 for x in deals)
    finally:
        for s in servers:
            s.shutdown()
