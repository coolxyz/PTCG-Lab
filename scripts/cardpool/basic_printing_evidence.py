"""Recover numbered Basic Energy rows omitted by the catalog projection."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from scripts.cardpool.catalog_inventory import product_tables

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def product_rows(cache_path, sha256, title):
    raw = (ROOT / cache_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        return []
    payload = json.loads(raw)
    html = payload.get("parse", {}).get("text", {})
    html = html.get("*", "") if isinstance(html, dict) else html
    return product_tables(html, title)[0]


@lru_cache(maxsize=1)
def evidence_index():
    result = {}
    inventory = json.loads((ROOT / "artifacts/cardpool/catalog-inventory.json").read_text(encoding="utf8"))
    for row in inventory["records"]:
        if row.get("facts", {}).get("basicEnergyType") and row.get("collectorNumber"):
            result.setdefault((row.get("productCode"), row["collectorNumber"]), []).append(row)
    return result


def verified_row(card):
    for row in evidence_index().get((card["productCode"], card["collectorNumber"]), []):
        if row.get("cardPage") != card["cardPage"] or row.get("cnName") != card["cnName"]:
            continue
        for evidence in row.get("sourceEvidence", []):
            source = evidence.get("source", {})
            if evidence.get("method") != "product-table" or not source.get("cachePath") or not source.get("sha256"):
                continue
            rows = product_rows(source["cachePath"], source["sha256"], source.get("title", ""))
            if any(not r.get("foreign") and all(r.get(k) == card.get(k) for k in
                   ("productCode", "collectorNumber", "cardPage", "cnName")) for r in rows):
                return {"cachePath": source["cachePath"], "sha256": source["sha256"],
                        "table": evidence["table"], "row": evidence["row"]}
    return None
