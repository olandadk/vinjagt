from __future__ import annotations

import logging

from . import generic, shopify, woocommerce

log = logging.getLogger(__name__)

PLATFORMS = {"shopify": shopify, "woocommerce": woocommerce, "generic": generic}


def detect(client, base: str) -> str | None:
    """Prøv de billige API'er først; ellers sitemap + schema.org."""
    for name in ("shopify", "woocommerce", "generic"):
        try:
            if PLATFORMS[name].detect(client, base.rstrip("/")):
                return name
        except Exception as e:  # noqa: BLE001
            log.debug("detect %s %s: %s", name, base, e)
    return None
