"""Prisalarm: hvilke tilbud meldes, og at samme tilbud kun meldes én gang."""
import json
from datetime import date

from vinjagt import alerts


def _deal(n, s, sd, of=True, p1=100.0):
    return {"n": n, "pr": None, "s": s, "sd": sd, "of": of, "p1": p1, "d": 20, "u": f"https://x.dk/{n}"}


def test_pick():
    today = date.today().isoformat()
    deals = [_deal("Barolo Riserva", 50, today), _deal("Château Musar", 90, today), _deal("Rioja", 90, "2020-01-01"),
             _deal("Chablis", 95, today, of=False), _deal("Barolo dyr", 50, today, p1=400)]
    got = {d["n"]: why for d, why in alerts.pick(deals, {"min_score": 80, "watch": [{"q": "barolo", "max": 250}]}, today)}
    assert got == {"Barolo Riserva": "på din liste (barolo)", "Château Musar": "score 90"}
    assert alerts._matches(_deal("Château Margaux", 1, today), [{"q": "chateau"}])  # uden accenter


def test_send_once(tmp_path, monkeypatch):
    today = date.today().isoformat()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "deals.json").write_text(json.dumps([_deal("Château Musar", 90, today)]))
    posts = []

    class R:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(alerts.requests, "post", lambda url, **kw: posts.append((url, kw)) or R())
    cfg = {"alerts": {"ntfy_topic": "test-emne", "min_score": 80}}
    assert alerts.send(cfg, str(tmp_path), str(tmp_path)) == 1
    assert posts[0][0] == "https://ntfy.sh/test-emne" and "Château Musar" in posts[0][1]["data"].decode()
    assert alerts.send(cfg, str(tmp_path), str(tmp_path)) == 0  # allerede meldt
    assert alerts.send({"alerts": {}}, str(tmp_path), str(tmp_path)) == 0  # slået fra
