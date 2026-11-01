from vinjagt import extract as X
from vinjagt.matching import similar, tokens, group_wines


def test_parse_price():
    assert X.parse_price("1.299,95 kr.") == 1299.95
    assert X.parse_price("149,-") == 149
    assert X.parse_price("149.00") == 149
    assert X.parse_price("kr. 89,95") == 89.95
    assert X.parse_price("1.299") == 1299
    assert X.parse_price("") is None


def test_quantity_prices():
    assert X.parse_quantity_prices("Ved køb af 6 stk.: 89,95 kr. pr. stk.", 119.95) == [(6, 89.95)]
    assert X.parse_quantity_prices("6 fl. for 499 kr", 119) == [(6, 83.17)]
    assert X.parse_quantity_prices("Kun 79 kr/fl. ved 6 stk", 99) == [(6, 79.0)]
    assert X.parse_quantity_prices("Pris v. 12 stk. 69,95", 99) == [(12, 69.95)]
    # urealistisk (højere end stykpris) ignoreres
    assert X.parse_quantity_prices("6 fl. for 999", 100) == []
    assert X.price_for_n(100, [(6, 80), (12, 70)], 6) == 80


def test_before_price():
    assert X.parse_before_price("Førpris: 149,95 Nu 99,95", 99.95) == (149.95, "førpris")
    assert X.parse_before_price("Vejl. pris 200,-", 120) == (200, "vejledende")
    assert X.parse_before_price("Normalpris 90", 120) == (None, None)


def test_vintage_volume_pack():
    assert X.parse_vintage("Barolo 2019 75 cl") == 2019
    assert X.parse_vintage("Champagne Brut NV") is None
    assert X.parse_volume_cl("Rioja Reserva 150 cl") == 150
    assert X.parse_volume_cl("Bordeaux Magnum") == 150
    assert X.parse_volume_cl("0,375 l") == 37.5
    assert X.parse_pack_size("Primitivo 6 x 75 cl") == 6


def test_classify():
    assert X.classify_type("Château Musar Rouge 2016") == "rød"
    assert X.classify_type("Cloudy Bay Sauvignon Blanc 2023") == "hvid"
    assert X.classify_type("Whispering Angel Rosé") == "rosé"
    assert X.classify_type("Bollinger Special Cuvée Brut Champagne") == "mousserende"
    assert X.classify_type("Graham's 10 Year Old Tawny Port") == "dessert"
    assert X.classify_type("Barolo DOCG 2019") == "rød"
    assert X.classify_type("Something", ["Rødvin", "Italien"]) == "rød"
    assert X.is_wine("Hendrick's Gin 70 cl", []) is False
    assert X.is_wine("Riedel Vinglas", ["Glas"]) is False
    assert X.is_wine("Barolo DOCG 2019", [], "rød") is True
    assert X.detect_country("Barolo DOCG 2019") == "Italien"


def test_critics():
    s = X.parse_critic_scores("93 point James Suckling. Robert Parker: 92. 5 stjerner i Politiken")
    d = {c["source"]: (c["score"], c["scale"]) for c in s}
    assert d["James Suckling"] == (93, 100)
    assert d["Robert Parker"] == (92, 100)
    assert d["Politiken"] == (5, 6)
    assert X.to_points(5, 6) == 90
    assert X.to_points(4.0, 5) == 90


def test_matching():
    a = tokens("Barolo DOCG 2019", "Fontanafredda")
    b = tokens("Fontanafredda Barolo 2019 75cl", None)
    assert similar(a, b) >= 0.82
    c = tokens("Chianti Classico Riserva", "Castello di Ama")
    d = tokens("Chianti Classico", "Castello di Ama")
    assert similar(c, d) == 0.0  # riserva adskiller
    g = group_wines([
        {"id": 1, "name": "Fontanafredda Barolo 2019", "vintage": 2019, "volume_cl": 75, "wine_type": "rød"},
        {"id": 2, "name": "Barolo DOCG", "producer": "Fontanafredda", "vintage": 2019, "volume_cl": 75, "wine_type": "rød"},
        {"id": 3, "name": "Fontanafredda Barolo 2018", "vintage": 2018, "volume_cl": 75, "wine_type": "rød"},
    ])
    assert g[1] == g[2] and g[3] != g[1]


def test_offer_ranking():
    from vinjagt.platforms.generic import _rank_offer
    assert _rank_offer("https://x.dk/tilbud", "Tilbud") > _rank_offer("https://x.dk/tilbud/smagekasser-pakketilbud", "Smagekasser")
    assert _rank_offer("https://x.dk/shop/singular-tawny-port-2890p.html", "Tilbud") == 0
    assert _rank_offer("https://x.dk/collections/alle-tilbud/products/chateau-x", "Tilbud") == 0
    assert _rank_offer("https://x.dk/oevrige-vine/varekort/brugnon", "Tilbud") == 0
    assert _rank_offer("https://x.dk/handelsbetingelser", "Tilbud") == 0
    assert _rank_offer("https://x.dk/alle-tilbud", "Alle tilbud") >= 3


def test_philipson_case_price():
    t = "Stk. pris ved køb af 6+ fl. 1 flaske 324,95 6+ flasker 109,00 324,95 Spar 215,95 DKK pr. fl."
    assert X.parse_quantity_prices(t, 324.95) == [(6, 109.0)]
    from vinjagt.models import Product, normalize
    w = normalize(Product("r", "u", "2021 Brunello di Montalcino Cordella", 324.95, description=t))
    assert w.price_6 == 109.0 and w.on_offer


def test_jsonld_lowest_price_fixed_by_single_price():
    from vinjagt.platforms.generic import parse_product_page
    html = ("<html><head><script type='application/ld+json'>"
            '{"@type":"Product","name":"2021 Brunello di Montalcino Cordella","brand":{"name":"Cordella"},'
            '"offers":{"@type":"Offer","price":"109.00","priceCurrency":"DKK"}}</script></head><body>'
            "<h1>2021 Brunello di Montalcino Cordella</h1><div>109,00 DKK <s>324,95</s> Stk. pris ved køb af 6+ fl.</div>"
            "<div>1 flaske 324,95</div><div>6+ flasker 109,00 324,95 Spar 215,95 DKK pr. fl.</div></body></html>")
    p = parse_product_page(html, "https://x.dk/p", "r")
    assert p.price == 324.95 and (6, 109.0) in p.qty_tiers
    from vinjagt.models import normalize
    w = normalize(p)
    assert w.price_1 == 324.95 and w.price_6 == 109.0 and w.on_offer


def test_dkk_prefixed_case_prices():
    from vinjagt.platforms.generic import text_after_h1
    t = "1 FLASKE DKK 150,00 Tilbudspris 6 FLASKER DKK 145,00 /fl. Spar DKK 5 pr. fl. 12 FLASKER DKK 145,00 /fl."
    html = f"<html><body><h1>Barbera</h1><p>{t}</p><div class='related'>6 flasker 120</div></body></html>"
    assert X.parse_quantity_prices(text_after_h1(html), 150.0) == [(6, 145.0), (12, 145.0)]


def test_german_shop_parsing():
    assert X.classify_type("2022 Spätburgunder trocken Rotwein") == "rød"
    assert X.classify_type("Riesling Weißwein feinherb 2023") == "hvid"
    assert X.classify_type("Winzersekt Brut") == "mousserende"
    assert X.parse_before_price("statt 14,90 € jetzt 9,90 €", 9.90) == (14.90, "førpris")
    assert X.parse_before_price("UVP 19,90 €", 12.90)[1] == "vejledende"
    assert X.parse_quantity_prices("ab 6 Flaschen je 8,90 €", 9.90) == [(6, 8.9)]
    from vinjagt.models import Product, normalize
    w = normalize(Product("r", "u", "2022 Spätburgunder trocken", 9.90, before_price=14.90, qty_tiers=[(6, 8.90)],
                          description="statt 14,90 €"), fx=7.46)
    assert w.price_1 == 73.85 and w.price_6 == 66.39 and w.before_price == 111.15


def test_vintage_punctuation_and_large_bottles():
    assert X.parse_vintage("Chianti Classico 2019.") == 2019
    assert X.parse_vintage("Barolo 2016, Piemonte") == 2016
    assert X.parse_vintage("Rioja 2018 årgang") == 2018
    assert X.parse_vintage("Pris 2019,95 kr") is None  # pris, ikke årgang
    assert X.parse_volume_cl("Bollinger Double Magnum") == 300
    assert X.parse_volume_cl("Barolo Magnum") == 150
    assert X.classify_type("Moët & Chandon Brut Impérial") == "mousserende"


def test_history_summary(tmp_path):
    import time
    from datetime import date, timedelta
    from vinjagt.db import DB
    db = DB(str(tmp_path / "h.sqlite"))
    db.c.execute("INSERT INTO wines(id,url,price_1,last_seen) VALUES(1,'u',100,?)", (time.time(),))
    for d in range(40):  # 130 kr indtil for 5 dage siden, derefter 100 kr
        db.c.execute("INSERT INTO price_history VALUES(1,?,?,NULL,NULL)",
                     ((date.today() - timedelta(days=d)).isoformat(), 100.0 if d < 5 else 130.0))
    h = db.history_summary()[1]
    assert h["n"] == 39 and h["med90"] == 130 and h["min30"] == 100 and h["lo90"] == 100
    assert h["since"] == (date.today() - timedelta(days=4)).isoformat()
    assert h["weeks"][-1] == 100 and h["weeks"][-6] == 130 and h["weeks"][0] is None
