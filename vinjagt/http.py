"""Høflig HTTP-klient: respekterer robots.txt, holder pause mellem kald pr. vært og prøver igen ved fejl."""
from __future__ import annotations

import gzip
import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)


class Blocked(Exception):
    """URL er ikke tilladt af robots.txt."""


class PoliteClient:
    def __init__(self, user_agent: str, delay: float = 1.5, timeout: float = 25, retries: int = 3,
                 respect_robots: bool = True):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "da-DK,da;q=0.9,en;q=0.6",
            # Kun HTML/XML som standard – nogle shops svarer ellers med JSON (get_json beder selv om JSON)
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
        })
        # Mange danske vinbutikker viser en alders-popup; cookies her springer den ofte over.
        self.s.cookies.set("age_verified", "1")
        self.s.cookies.set("ageVerified", "true")
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self.ua_token = user_agent.split("/")[0]
        self.requests_made = 0

    # -------------------------------------------------- robots.txt
    def robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code >= 400:
                    rp = None  # ingen robots.txt -> alt tilladt
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp = None
            self._robots[base] = rp
        return self._robots[base]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self.robots(url)
        return True if rp is None else rp.can_fetch(self.ua_token, url)

    def crawl_delay(self, url: str) -> float:
        rp = self.robots(url) if self.respect_robots else None
        d = None
        if rp is not None:
            try:
                d = rp.crawl_delay(self.ua_token)
            except Exception:
                d = None
        return max(self.delay, float(d or 0))

    def sitemaps(self, url: str) -> list[str]:
        rp = self.robots(url)
        try:
            return list(rp.site_maps() or []) if rp else []
        except Exception:
            return []

    # -------------------------------------------------- hentning
    def _wait(self, url: str):
        host = urlparse(url).netloc
        delay = self.crawl_delay(url)
        with self._lock:
            last = self._last.get(host, 0)
            wait = last + delay - time.monotonic()
            self._last[host] = max(time.monotonic(), last + delay)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, **kw) -> requests.Response:
        if not self.allowed(url):
            raise Blocked(url)
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait(url)
            try:
                self.requests_made += 1
                r = self.s.get(url, timeout=self.timeout, **kw)
                if r.status_code in (429, 503) and attempt < self.retries:
                    ra = r.headers.get("Retry-After", "")
                    wait = int(ra) if ra.isdigit() else 15 * (attempt + 1)
                    log.info("%s svarede %s – venter %ss", urlparse(url).netloc, r.status_code, min(wait, 120))
                    time.sleep(min(wait, 120))
                    # sænk tempoet for denne vært resten af kørslen
                    self.delay = max(self.delay, 3.0)
                    continue
                return r
            except requests.RequestException as e:
                err = e
                time.sleep(3 * (attempt + 1))
        raise err or RuntimeError(url)

    def get_json(self, url: str, **kw):
        r = self.get(url, headers={"Accept": "application/json"}, **kw)
        if r.status_code != 200:
            return None, r
        try:
            return r.json(), r
        except ValueError:
            return None, r

    def get_text(self, url: str) -> str | None:
        r = self.get(url)
        if r.status_code != 200:
            return None
        if url.endswith(".gz") and r.content[:2] == b"\x1f\x8b":
            return gzip.decompress(r.content).decode("utf-8", "replace")
        r.encoding = r.encoding or r.apparent_encoding
        return r.text
