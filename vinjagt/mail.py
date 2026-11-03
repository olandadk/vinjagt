"""Nyhedsbreve: læs en (dedikeret) postkasse via IMAP og find links til vine hos de scannede forhandlere.

Kun links gemmes – prisen læses altid på forhandlerens egen side ved næste scanning.
Klik-registrerings-links (Mailchimp, Klaviyo, ...) følges for at finde den rigtige adresse.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger(__name__)

SKIP = re.compile(r"unsubscribe|afmeld|abmelden|preferences|praeferencer|præferencer|view.?in.?browser|webversion|"
                  r"vis.?i.?browser|facebook\.com|instagram\.com|linkedin\.com|youtube\.com|tiktok\.com|twitter\.com|"
                  r"x\.com/|mailto:|tel:|apps\.apple\.com|play\.google\.com|trustpilot|privacy|privatliv|cookie", re.I)
# Kun disse parametre beholdes – alt andet (utm_, tw_profile_id, _kx ...) er sporing og kan identificere postkassen
KEEP_PARAMS = {"variant", "size", "vintage", "year", "aargang", "id", "pid", "product", "sku"}
JUNK = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|css|js|pdf)(\?|$)|getimage|/image|/media/|/files/|/cdn/|/assets/|"
                  r"opret|bruger|login|log-ind|konto|account|register|signup|nyhedsbrev|newsletter|kurv|cart|checkout|"
                  r"kontakt|contact|handelsbetingelser|terms|faq|om-os|about|butikker|find-?butik|kundeservice", re.I)


def _clean(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in KEEP_PARAMS]
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(q), ""))


def is_junk(url: str) -> bool:
    return bool(JUNK.search(urlparse(url).path + "?" + urlparse(url).query)) or urlparse(url).path in ("", "/")


def _host(u: str) -> str:
    return urlparse(u).netloc.lower().replace("www.", "")


def _html_and_text(msg) -> str:
    out = []
    for part in msg.walk():
        if part.get_content_type() in ("text/html", "text/plain") and not part.get_filename():
            try:
                out.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace"))
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(out)


def _links(body: str) -> list[str]:
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', body, flags=re.I)
    hrefs += re.findall(r"https?://[^\s<>\"')\]]+", body)
    return list(dict.fromkeys(h.replace("&amp;", "&") for h in hrefs if h.startswith("http") and not SKIP.search(h)))


def fetch(db, cfg: dict, retailers: list[dict], client) -> int:
    """Læs nye mails og gem vin-links pr. forhandler. Returnerer antal gemte links."""
    m = cfg.get("mail") or {}
    if not m.get("enabled"):
        return 0
    hosts = {_host(r["url"]): r["id"] for r in retailers if r.get("enabled", True)}
    db.clean_mail_links(_clean, is_junk)
    seen_ids = db.mail_seen_ids()
    imap = imaplib.IMAP4_SSL(m["host"], int(m.get("port", 993)))
    try:
        imap.login(m["user"], m["password"])
        imap.select(m.get("folder", "INBOX"), readonly=True)
        since = time.strftime("%d-%b-%Y", time.gmtime(time.time() - int(m.get("days", 14)) * 86400))
        _, data = imap.search(None, "SINCE", since)
        ids = data[0].split() if data and data[0] else []
        saved = 0
        for num in ids[-200:]:
            _, raw = imap.fetch(num, "(RFC822)")
            if not raw or not raw[0]:
                continue
            msg = email.message_from_bytes(raw[0][1])
            mid = msg.get("Message-ID") or f"{msg.get('From')}|{msg.get('Date')}|{msg.get('Subject')}"
            if mid in seen_ids:
                continue
            subject = str(make_header(decode_header(msg.get("Subject") or "")))[:200]
            try:
                received = parsedate_to_datetime(msg.get("Date")).timestamp()
            except Exception:  # noqa: BLE001
                received = time.time()
            links = _links(_html_and_text(msg))
            found = 0
            followed = 0
            for link in links:
                rid = hosts.get(_host(link))
                url = link
                if not rid and followed < int(m.get("follow_limit", 40)):
                    # klik-registrering: følg omdirigeringen for at finde den rigtige adresse
                    followed += 1
                    try:
                        r = client.s.get(link, allow_redirects=True, timeout=15, stream=True)
                        url = r.url
                        r.close()
                    except Exception:  # noqa: BLE001
                        continue
                    rid = hosts.get(_host(url))
                if not rid or is_junk(url):
                    continue
                db.save_mail_link(_clean(url), rid, subject, received)
                found += 1
            db.mark_mail_seen(mid, subject, received, found)
            saved += found
            log.info("Nyhedsbrev: %s – %d vin-links", subject[:70], found)
        db.commit()
        return saved
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
