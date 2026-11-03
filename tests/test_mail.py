"""Nyhedsbreve: links findes, rengøres og knyttes til forhandlere – uden rigtig IMAP-server."""
import os
import sys
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(__file__))

from vinjagt import mail  # noqa: E402
from vinjagt.db import DB  # noqa: E402


def _msg(html, subject="Ugens tilbud"):
    m = MIMEText(html, "html", "utf-8")
    m["Subject"], m["From"], m["Message-ID"] = subject, "nyhed@shop.dk", "<abc@shop.dk>"
    m["Date"] = "Tue, 03 Nov 2026 08:00:00 +0100"
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, *a): pass
    def login(self, *a): pass
    def select(self, *a, **k): pass
    def search(self, *a): return "OK", [b"1"]
    def fetch(self, *a): return "OK", [(b"1", FakeIMAP.raw)]
    def logout(self): pass


class FakeClient:
    class s:  # noqa: N801
        @staticmethod
        def get(url, **k):
            class R:
                def close(self): pass
            r = R()
            r.url = "https://www.shop.dk/vin/barolo-2019?utm_source=nl" if "click.mail" in url else url
            return r


def test_mail_links(tmp_path, monkeypatch):
    FakeIMAP.raw = _msg('<a href="https://click.mail.example/abc">Barolo</a>'
                        '<a href="https://www.shop.dk/vin/chianti?utm_campaign=x&size=75">Chianti</a>'
                        '<a href="https://www.shop.dk/afmeld">Afmeld</a><a href="https://facebook.com/shop">fb</a>'
                        '<a href="https://www.shop.dk/">Forside</a>')
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", FakeIMAP)
    db = DB(str(tmp_path / "t.sqlite"))
    cfg = {"mail": {"enabled": True, "host": "x", "user": "u", "password": "p"}}
    n = mail.fetch(db, cfg, [{"id": "shop", "url": "https://www.shop.dk"}], FakeClient())
    urls = sorted(m["url"] for m in db.mail_links(10000))
    assert n == 2
    assert urls == ["https://www.shop.dk/vin/barolo-2019", "https://www.shop.dk/vin/chianti?size=75"]
    assert mail.fetch(db, cfg, [{"id": "shop", "url": "https://www.shop.dk"}], FakeClient()) == 0  # samme mail igen
