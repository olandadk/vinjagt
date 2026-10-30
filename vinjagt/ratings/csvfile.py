"""Egne kilder via CSV – fx point fra Politiken/Berlingske, Decanter World Wine Awards-lister eller egne noter.

Kolonner (semikolon eller komma):  source;name;producer;vintage;rating;scale;count;price;url
  source   – kildens navn, fx "Politiken"
  rating   – vurderingen på kildens skala
  scale    – 100, 20, 6 eller 5
"""
from __future__ import annotations

import csv
import glob
import logging
import os

from ..extract import parse_price
from ..matching import wine_key

log = logging.getLogger(__name__)


def load(db, folder: str) -> int:
    n = 0
    for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
        with open(path, encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
            for i, row in enumerate(csv.DictReader(f, dialect=dialect)):
                try:
                    rating = float(str(row.get("rating", "")).replace(",", "."))
                    scale = float(row.get("scale") or 100)
                except ValueError:
                    continue
                name = row.get("name") or ""
                producer = row.get("producer") or None
                vintage = int(row["vintage"]) if (row.get("vintage") or "").isdigit() else 0
                src = row.get("source") or os.path.splitext(os.path.basename(path))[0]
                db.save_ext(src, f"{os.path.basename(path)}:{i}", name, producer, vintage, None, rating, scale,
                            int(row["count"]) if (row.get("count") or "").isdigit() else None,
                            parse_price(row.get("price")), row.get("url") or None, wine_key(name, producer))
                n += 1
    db.commit()
    if n:
        log.info("CSV-vurderinger: %d rækker indlæst fra %s", n, folder)
    return n
