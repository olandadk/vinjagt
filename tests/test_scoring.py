import json
import math
import random

from vinjagt.scoring import evaluate


def _wine(i, price, critics, wine_type="red"):
    return {"id": i, "name": f"Vin {i}", "retailer_id": f"shop{i % 3}", "url": f"https://x/{i}", "in_stock": 1,
            "price_1": price, "price_6": None, "before_price": price * 1.3, "before_kind": "før", "on_offer": 1,
            "wine_type": wine_type, "critic": json.dumps([{"source": s, "score": sc, "scale": 100} for s, sc in critics])}


def _data(dessert_offset=0.0):
    rnd = random.Random(1)
    wines = []
    for i in range(1, 121):
        price = rnd.choice([79, 99, 129, 149, 199, 249, 299, 399])
        true = 80 + 2 * math.log(price) + rnd.gauss(0, 1)
        src = ["Generøs", "Streng", "Neutral"][i % 3]
        bias = {"Generøs": 3.0, "Streng": -1.0, "Neutral": 0.0}[src]
        wines.append(_wine(i, price, [(src, round(true + bias))]))
    for i in range(200, 240):  # dessertvin: dyrere for samme point
        price = rnd.choice([149, 199, 249, 299])
        wines.append(_wine(i, price, [("Neutral", round(80 + 2 * math.log(price) - 3 + rnd.gauss(0, 1)))], "dessert"))
    return wines


def test_calibration_and_dessert_curve():
    wines = _data()
    deals, stats = evaluate(wines, {}, {}, [], {f"shop{i}": {} for i in range(3)},
                            {"min_discount": 0.0, "min_value_points": -99})
    kal = stats["kalibrering"]
    # den generøse anmelder trækkes ned, den strenge op – og forskellen mellem dem er tæt på de indlagte 4 point
    assert kal["Generøs"]["offset"] > kal["Neutral"]["offset"] > kal["Streng"]["offset"]
    assert 2.5 < kal["Generøs"]["offset"] - kal["Streng"]["offset"] < 4.5
    # dessert har egen kurve, så dessertvinene ligger omkring 0 i værdi i stedet for systematisk negativt
    assert "dessert" in stats["regression_typer"]
    dvals = [d["v"] for d in deals if d["t"] == "dessert" and d["v"] is not None]
    assert dvals and abs(sum(dvals) / len(dvals)) < 1.0
    # kalibreret point er vist på karakteren, den rå karakter bevares
    r = next(r for d in deals for r in d["rt"] if r["s"] == "Generøs")
    assert r.get("k") and r["v"].endswith(" p")
