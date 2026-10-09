"""Build a source-backed catalogue snapshot and queryable SQLite database."""

from pathlib import Path
import sys, json, hashlib, sqlite3, collections

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.sources import ROOT


def main():
    src = json.loads((ROOT / "data/catalog/enriched.json").read_text())
    images_path = ROOT / "data/catalog/image-mappings.json"
    image_data = json.loads(images_path.read_text()) if images_path.exists() else {}
    mappings = image_data.get("mappings", {})
    cn_path = ROOT / "data/catalog/cn151-images.json"
    if cn_path.exists():
        mappings.update(json.loads(cn_path.read_text())["mappings"])
    baseline = json.loads((ROOT / "data/collection/catalog.json").read_text())
    cards = {c["printingId"]: dict(c) for c in baseline["cards"]}
    for c in src["cards"]:
        pid = c["printingId"]
        metadata = {
            k: v
            for k, v in c.items()
            if k not in ("source", "cardSource", "imageCandidates")
        }
        metadata.update(
            engineId="unverified:" + pid,
            definitionId="unverified:" + pid,
            nameLimitKey=c["cnName"],
            aliases=[c["cardPage"]] if c.get("cardPage") else [],
            englishName=c.get("englishName", ""),
            identityKind="numbered-printing",
            isBasicPokemon=c.get("isBasicPokemon")
            if c["category"] == "宝可梦"
            else False,
            basicEnergyType=c.get("basicEnergyType"),
            aceSpec=bool(c.get("aceSpec")),
            legacyReprintVerified=False,
            evolvesFrom=c.get("evolvesFrom", []),
            stage=c.get("stage"),
            attacks=c.get("attacks", []),
            pokemonType=c.get("pokemonType") or "NONE",
            hp=c.get("hp"),
            mark=c.get("mark"),
            sourceEvidence=list(
                dict.fromkeys(
                    u
                    for u in [
                        c["source"]["url"],
                        c.get("cardSource", {}).get("sourceUrl"),
                    ]
                    if u
                )
            ),
            reviewNote="百科卡表已收录；效果实现与赛制资格尚未审核。图片仅作对应印刷或同效果繁中替代展示。",
            images=[],
            sourceVerified=False,
            effectStatus="unverified",
        )
        if c.get("numberCorrection"):
            metadata["sourceEvidence"] += c["numberCorrection"]["evidenceUrls"]
            metadata["reviewNote"] = (
                "编号经本地人工交叉校订："
                + c["numberCorrection"]["oldPrintedNumber"]
                + " → "
                + c["printedNumber"]
                + "。"
                + metadata["reviewNote"]
            )
        im = mappings.get(pid)
        if im:
            metadata["images"] = [
                {**im, "remoteUrl": im["url"], "url": im.get("localUrl", im["url"])}
            ]
            metadata["sourceEvidence"] += [im["source"], im["fileEvidence"]]
        if pid in cards:
            # Certification always comes only from the curated baseline.
            if not cards[pid].get("images"):
                cards[pid]["images"] = metadata["images"]
            cards[pid]["productIds"] = c["productIds"]
        else:
            cards[pid] = metadata
    products = [
        {
            k: p.get(k)
            for k in (
                "id",
                "name",
                "title",
                "releasedAt",
                "tableCount",
                "status",
                "source",
            )
        }
        for p in src["products"]
        if p["status"] == "released"
    ]
    payload = {
        "cards": list(cards.values()),
        "products": products,
        "templates": baseline["templates"] + json.loads(
            (ROOT / "data/cardpool/test-decks.json").read_text(encoding="utf-8")
        ),
        "formatId": baseline["formatId"],
        "asOf": src["asOf"],
    }
    digest = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    payload["version"] = digest
    dest = ROOT / "data/catalog/catalog.json"
    staged = dest.with_suffix(".tmp.json")
    staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    db = ROOT / "data/catalog/catalog.sqlite"
    tmp = db.with_suffix(".tmp.sqlite")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript("""PRAGMA foreign_keys=ON;
    CREATE TABLE products(id TEXT PRIMARY KEY,name TEXT,release_date TEXT,source_url TEXT,revision INTEGER,card_count INTEGER);
    CREATE TABLE cards(printing_id TEXT PRIMARY KEY,product_code TEXT,collector_number TEXT,name TEXT,category TEXT,effect_status TEXT,metadata_json TEXT NOT NULL);
    CREATE INDEX cards_name ON cards(name);CREATE INDEX cards_set ON cards(product_code,collector_number);
    CREATE TABLE card_products(printing_id TEXT REFERENCES cards,product_id TEXT REFERENCES products,PRIMARY KEY(printing_id,product_id));
    CREATE TABLE source_rows(id INTEGER PRIMARY KEY,printing_id TEXT,product_id TEXT,source_json TEXT);
    CREATE TABLE number_corrections(id TEXT PRIMARY KEY,evidence_json TEXT);
    CREATE TABLE conflicts(printing_id TEXT PRIMARY KEY,evidence_json TEXT);
    CREATE TABLE image_mappings(printing_id TEXT PRIMARY KEY REFERENCES cards,locale TEXT,source_url TEXT,image_url TEXT,sha256 TEXT,metadata_json TEXT);
    CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
    """)
    for p in products:
        con.execute(
            "INSERT INTO products VALUES(?,?,?,?,?,?)",
            (
                p["id"],
                p["name"],
                p["releasedAt"],
                p["source"]["url"],
                p["source"]["revision"],
                p["tableCount"],
            ),
        )
    for c in cards.values():
        con.execute(
            "INSERT INTO cards VALUES(?,?,?,?,?,?,?)",
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
            con.execute(
                "INSERT INTO card_products VALUES(?,?)", (c["printingId"], product)
            )
    for r in src["sourceRows"]:
        con.execute(
            "INSERT INTO source_rows(printing_id,product_id,source_json) VALUES(?,?,?)",
            (r["printingId"], r["productId"], json.dumps(r, ensure_ascii=False)),
        )
    for correction in src.get("corrections", []):
        con.execute(
            "INSERT INTO number_corrections VALUES(?,?)",
            (correction["id"], json.dumps(correction, ensure_ascii=False)),
        )
    for pid, rows in src["conflicts"].items():
        con.execute(
            "INSERT INTO conflicts VALUES(?,?)",
            (pid, json.dumps(rows, ensure_ascii=False)),
        )
    for pid, im in mappings.items():
        con.execute(
            "INSERT INTO image_mappings VALUES(?,?,?,?,?,?)",
            (
                pid,
                im["locale"],
                im["source"],
                im["url"],
                im.get("sha256"),
                json.dumps(im, ensure_ascii=False),
            ),
        )
    for k, v in [
        ("version", payload["version"]),
        ("asOf", src["asOf"]),
        ("dataSource", "神奇宝贝百科 / Pokémon官方网站"),
    ]:
        con.execute("INSERT INTO metadata VALUES(?,?)", (k, v))
    con.commit()
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()
    tmp.replace(db)
    staged.replace(dest)
    report = {
        "asOf": src["asOf"],
        "version": payload["version"],
        "discoveredProductPages": len(
            json.loads((ROOT / "data/catalog/product-index.json").read_text())[
                "products"
            ]
        ),
        "releasedPackTables": len(products),
        "sourceRows": len(src["sourceRows"]),
        "uniqueUnconflictedPackPrintings": len(src["cards"]),
        "applicationCards": len(cards),
        "manualNumberCorrections": len(src.get("corrections", [])),
        "conflictingNumbers": len(src["conflicts"]),
        "quarantinedRows": sum(len(v) for v in src["conflicts"].values()),
        "matchedArtwork": len(mappings),
        "localArtworkMappings": sum(
            bool(im.get("localUrl")) for im in mappings.values()
        ),
        "artworkLocales": dict(
            collections.Counter(m["locale"] for m in mappings.values())
        ),
        "missingArtwork": len(src["cards"]) - len(mappings),
        "artworkGapReasons": dict(
            collections.Counter(
                g["reason"]
                for pid, g in image_data.get("gaps", {}).items()
                if pid not in mappings
            )
        ),
        "officialSourceErrors": json.loads(
            (ROOT / "data/catalog/official-artwork.json").read_text()
        ).get("errors", {})
        if (ROOT / "data/catalog/official-artwork.json").exists()
        else {},
        "unknownPokemonStages": sum(
            c["category"] == "宝可梦" and c["isBasicPokemon"] is None
            for c in cards.values()
        ),
        "unparsedRows": [
            {"product": p["name"], "rows": p["unparsedRows"]}
            for p in src["products"]
            if p.get("unparsedRows")
        ],
        "sourceErrors": [
            p["name"] for p in src["products"] if p["status"] == "source_error"
        ],
        "metadata": src["enrichment"],
        "engineVerified": sum(c["effectStatus"] == "verified" for c in cards.values()),
        "products": [
            {"name": p["name"], "rows": p["tableCount"], "releasedAt": p["releasedAt"]}
            for p in products
        ],
        "issues": src["issues"],
    }
    (ROOT / "artifacts/catalog/coverage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        {
            k: v
            for k, v in report.items()
            if k not in ("products", "issues", "officialSourceErrors")
        }
    )


if __name__ == "__main__":
    main()
