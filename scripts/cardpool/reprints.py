"""Publish only exact article/printing reprints of the curated engine effects.

This does not certify new effects or infer equivalence from card names.
"""

import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def propose(inventory, catalog, baseline, release):
    base = {
        c["printingId"]: c
        for c in baseline["cards"]
        if c.get("sourceVerified") and c.get("effectStatus") == "verified"
    }
    anchors = {}
    for row in inventory["cards"]:
        # Existing anchors were reviewed against the preconstructed product rows;
        # card articles often omit those preconstructed ExpansionList entries.
        # New printings, below, still require their own exact article pairing.
        if row["printingId"] in base and not set(row["gaps"]) - {
            "RELEASE_EVIDENCE_REQUIRED",
            "PRINTING_PAIR_UNRESOLVED",
        }:
            anchors.setdefault(row["cardPage"], []).append(row)
    cards = {c["printingId"]: copy.deepcopy(c) for c in catalog["cards"]}
    updated_release = copy.deepcopy(release)
    products = copy.deepcopy(catalog.get("products", []))
    by_title = {p["title"]: p for p in products}
    effects = {e["effectKey"]: e for e in updated_release["effects"]}
    reviews = []
    for row in inventory["cards"]:
        pid = row["printingId"]
        if (
            pid in base
            or row["releaseStatus"] != "released"
            or row["formatStatus"] not in ("current-mark", "basic-energy")
        ):
            continue
        if set(row["gaps"]) - {"PRINTING_REVIEW_REQUIRED", "RULE_EFFECT_UNREVIEWED"}:
            continue
        candidates = anchors.get(row["cardPage"], [])
        if (
            not candidates
            or len({base[a["printingId"]]["engineId"] for a in candidates}) != 1
        ):
            continue
        anchor = candidates[0]
        if row["facts"] != anchor["facts"] or row["cardSource"] != anchor["cardSource"]:
            continue
        original = base[anchor["printingId"]]
        effect = effects.get(original["engineId"])
        if not effect or original["printingId"] not in effect["printings"]:
            continue
        old = cards.get(pid, {})
        card = copy.deepcopy(original)
        # Artwork always belongs to this printing, never to the anchor.
        card.update(
            {
                k: row[k]
                for k in (
                    "printingId",
                    "collectorNumber",
                    "productCode",
                    "cnName",
                    "mark",
                    "cardPage",
                )
            }
        )
        bounds = [s["releaseBound"] for s in row["sources"] if s.get("releaseBound")]
        card.update(
            identityKind="numbered-printing",
            releasedAt=max(bounds),
            releaseDateKind="verified-released-by-bound",
            reviewScope="p4-equivalent-reprint-v1",
            images=old.get("images", []),
            productIds=old.get("productIds", []),
            sourceEvidence=[
                row["cardSource"]["sourceUrl"]
                + "?oldid="
                + str(row["cardSource"]["revision"])
            ]
            + [
                "https://wiki.52poke.com/index.php?title="
                + quote(s["source"]["title"])
                + "&oldid="
                + str(s["source"]["revision"])
                for s in row["sources"]
            ],
            reviewNote="简中编号与监管标记在同一规则页精确配对；复用已验收效果。发售日期为已发售证据上界，不代表首发日。",
            reprintAnchor=original["printingId"],
        )
        card["aliases"] = sorted(
            set(original.get("aliases", []) + [row["cnName"], row["cardPage"]])
        )
        for source in row["sources"]:
            title = source["product"]
            if title not in by_title:
                product = {
                    "id": f"p4:{source['source']['pageId']}",
                    "name": title.removesuffix("（TCG）"),
                    "title": title,
                    "releasedAt": source["releaseBound"],
                    "status": "released",
                    "source": source["source"],
                }
                by_title[title] = product
                products.append(product)
            card["productIds"] = sorted(
                set(card["productIds"]) | {by_title[title]["id"]}
            )
        cards[pid] = card
        effect["printings"] = sorted(set(effect["printings"]) | {pid})
        reviews.append(
            {
                "printingId": pid,
                "anchorPrintingId": original["printingId"],
                "effectKey": original["engineId"],
                "cardSource": row["cardSource"],
                "productSources": row["sources"],
                "factsHash": digest(row["facts"]),
                "mark": row["mark"],
            }
        )
    result = copy.deepcopy(catalog)
    result.update(cards=list(cards.values()), products=products, asOf=inventory["asOf"])
    result.pop("version", None)
    result["version"] = digest(result)
    updated_release["scope"] = (
        f"reviewed {sum(c.get('effectStatus') == 'verified' for c in cards.values())} identities / {len(effects)} effects; arbitrary legal compositions experimental"
    )
    return result, updated_release, reviews


def main():
    inventory_path = ROOT / "artifacts/cardpool/target-inventory.json"
    catalog_path = ROOT / "data/catalog/catalog.json"
    release_path = ROOT / "data/simulation/effects.json"
    inventory = read(inventory_path)
    # All review evidence must still match its frozen response bytes.
    checked = set()
    for row in inventory["cards"]:
        for src in [row.get("cardSource", {})] + [s["source"] for s in row["sources"]]:
            if not src.get("cachePath"):
                continue
            expected = src.get("responseSha256", src.get("sha256"))
            if (src["cachePath"], expected) in checked:
                continue
            if (
                hashlib.sha256((ROOT / src["cachePath"]).read_bytes()).hexdigest()
                != expected
            ):
                raise ValueError("SOURCE_CHANGED: " + src["cachePath"])
            checked.add((src["cachePath"], expected))
    previous = release_path.read_bytes()
    old_hash = hashlib.sha256(previous).hexdigest()
    catalog, release, reviews = propose(
        inventory,
        read(catalog_path),
        read(ROOT / "data/collection/catalog.json"),
        read(release_path),
    )
    if not reviews:
        raise ValueError("NO_REPRINTS_REVIEWABLE")
    report_path = ROOT / "data/cardpool/reprints.json"
    if (
        encoded(release) == previous
        and catalog == read(catalog_path)
        and report_path.exists()
    ):
        existing = read(report_path)
        if (
            existing["inventorySha256"]
            == hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        ):
            print(json.dumps({k: v for k, v in existing.items() if k != "reviews"}))
            return
    history = ROOT / "data/cardpool/release-history"
    history.mkdir(parents=True, exist_ok=True)
    archived = history / (old_hash + ".json")
    if archived.exists() and archived.read_bytes() != previous:
        raise ValueError("ARCHIVE_COLLISION")
    archived.write_bytes(previous)
    backup = ROOT / ".catalog/cardpool/pre-reprints"
    backup.mkdir(exist_ok=True)
    if not (backup / "catalog.json").exists():
        (backup / "catalog.json").write_bytes(catalog_path.read_bytes())
    database = ROOT / "data/catalog/catalog.sqlite"
    staged = database.with_suffix(".cardpool.sqlite")
    with (
        closing(sqlite3.connect(database)) as src,
        closing(sqlite3.connect(staged)) as dst,
    ):
        src.backup(dst)
        for p in catalog["products"]:
            dst.execute(
                "INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?)",
                (
                    p["id"],
                    p["name"],
                    p.get("releasedAt"),
                    p["source"]["url"],
                    p["source"]["revision"],
                    p.get("tableCount"),
                ),
            )
        for c in catalog["cards"]:
            dst.execute(
                "INSERT INTO cards VALUES(?,?,?,?,?,?,?) ON CONFLICT(printing_id) DO UPDATE SET product_code=excluded.product_code,collector_number=excluded.collector_number,name=excluded.name,category=excluded.category,effect_status=excluded.effect_status,metadata_json=excluded.metadata_json",
                (
                    c["printingId"],
                    c["productCode"],
                    c["collectorNumber"],
                    c["cnName"],
                    c["category"],
                    c["effectStatus"],
                    json.dumps(c, ensure_ascii=False),
                ),
            )
            for product in c.get("productIds", []):
                dst.execute(
                    "INSERT OR IGNORE INTO card_products VALUES(?,?)",
                    (c["printingId"], product),
                )
        for key in ("asOf", "version"):
            dst.execute("UPDATE metadata SET value=? WHERE key=?", (catalog[key], key))
        dst.commit()
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # Retain a recoverable original; publication occurs while the API is stopped.
    if not (backup / "catalog.sqlite").exists():
        (backup / "catalog.sqlite").write_bytes(database.read_bytes())
    staged.replace(database)
    catalog_path.write_bytes(encoded(catalog))
    release_path.write_bytes(encoded(release))
    report = {
        "schema": "p4-reprints-v1",
        "inventorySha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        "previousEffectRelease": old_hash,
        "effectRelease": hashlib.sha256(encoded(release)).hexdigest(),
        "catalogVersion": catalog["version"],
        "reviewedReprints": len(reviews),
        "reviews": reviews,
    }
    (ROOT / "data/cardpool/reprints.json").write_bytes(encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k != "reviews"}))


if __name__ == "__main__":
    main()
