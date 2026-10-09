"""Add all identifiable wiki catalogue records without granting battle support."""

from collections import Counter, defaultdict
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cardpool.catalog_inventory import cn_code, read, PROMOS, ENERGIES  # noqa: E402


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def proposed_id(row):
    energy_type = row.get("basicEnergyType") or row.get("facts", {}).get(
        "basicEnergyType"
    )
    if (
        not row.get("collectorNumber")
        and row.get("productCode") not in PROMOS
        and energy_type in ENERGIES.values()
    ):
        return "CN:basic:" + next(k for k, v in ENERGIES.items() if v == energy_type)
    if row.get("basicEnergyType") and row["printedNumber"] in (
        "GRA",
        "FIR",
        "WAT",
        "LIG",
        "PSY",
        "FIG",
        "DAR",
        "MET",
        "FAI",
    ):
        return "CN:basic:" + row["printedNumber"]
    if cn_code(row.get("productCode")) and row.get("collectorNumber"):
        return f"CN:{row['productCode']}:{row['collectorNumber']}"
    identity = [
        row.get("productCode"),
        row["cardPage"],
        row["cnName"],
        row.get("availabilityText", "") if row.get("productCode") in PROMOS else "",
        row["printedNumber"],
    ]
    if not row.get("productCode"):
        identity.append(sorted(e["productId"] for e in row["sourceEvidence"]))
    return "CN:source:" + digest(identity)[:20]


def source_url(source):
    return (
        "https://wiki.52poke.com/index.php?title="
        + quote(source["title"])
        + "&oldid="
        + str(source["revision"])
    )


def augment(catalog, inventory):
    result = copy.deepcopy(catalog)
    cards = {c["printingId"]: c for c in result["cards"]}
    before = set(cards)
    products = {p["title"]: p for p in result["products"]}
    product_ids = {}
    for source in inventory["products"]:
        title = source["title"]
        if title not in products:
            p = {k: source[k] for k in ("id", "title", "name", "releasedAt", "source")}
            p.update(
                status="listed"
                if not p["releasedAt"]
                else "released"
                if p["releasedAt"] <= inventory["asOf"]
                else "future",
                tableCount=sum(
                    t["namedRows"] for t in source["tables"] if not t["foreign"]
                ),
            )
            products[title] = p
            result["products"].append(p)
        product_ids[source["id"]] = products[title]["id"]
    groups = defaultdict(lambda: defaultdict(list))
    names = defaultdict(Counter)
    for row in inventory["records"]:
        groups[proposed_id(row)][row["cardPage"]].append(row)
        if any(
            e["method"] == "product-table" for e in row["sourceEvidence"]
        ) and row.get("collectorNumber"):
            names[row["cardPage"]][row["cnName"]] += 1
    assignments, conflicts = [], []
    for candidate, identities in sorted(groups.items()):
        # A canonical energy reference represents a type, not collectible art.
        if candidate.startswith("CN:basic:"):
            identities = {
                next(iter(identities)): [r for rs in identities.values() for r in rs]
            }
        existing = cards.get(candidate, {})
        anchor = existing.get("cardPage")
        if not anchor and existing:
            possible = [
                title
                for title, rows in identities.items()
                if any(r["cnName"] == existing["cnName"] for r in rows)
            ]
            anchor = possible[0] if len(possible) == 1 else None
        conflicting = len(identities) > 1
        if conflicting:
            conflicts.append(
                {
                    "claimedPrintingId": candidate,
                    "cardPages": sorted(identities),
                    "existingIdentityRetained": candidate in cards,
                }
            )
        for title, rows in sorted(identities.items()):
            pid = (
                candidate
                if not conflicting or title == anchor
                else "CN:source:" + digest([candidate, title])[:20]
            )
            prior = cards.get(pid, {})
            # Product names precede article-derived translations; paired article
            # metadata supplies category, HP, stage and regulation where known.
            best = max(
                rows,
                key=lambda r: (
                    bool(r.get("articlePaired")),
                    bool(r.get("cardSource", {}).get("revision")),
                ),
            )
            facts = best.get("facts", {})
            evidence = [e for r in rows for e in r["sourceEvidence"]]
            links = sorted(
                {product_ids[e["productId"]] for e in evidence if e["productId"]}
            )
            urls = {source_url(e["source"]) for e in evidence}
            for row in rows:
                if row.get("cardSource", {}).get("revision"):
                    urls.add(source_url(row["cardSource"]))
            # Existing reviews and engine/image identity survive byte-for-byte
            # for all their existing fields; only add product associations.
            if prior.get("effectStatus") == "verified":
                prior["productIds"] = sorted(
                    set(prior.get("productIds", [])) | set(links)
                )
                for r in rows:
                    assignments.append(
                        {
                            "printingId": pid,
                            "cardPage": title,
                            "evidence": r["sourceEvidence"],
                        }
                    )
                continue
            display_name = (
                names[title].most_common(1)[0][0]
                if names[title] and best.get("collectorNumber")
                else best["cnName"]
            )
            category = (
                facts.get("category")
                or best.get("category")
                or prior.get("category")
                or "未知"
            )
            if category == "未知" and prior.get("category"):
                category = prior["category"]
            basic = best.get("basicEnergyType") or facts.get("basicEnergyType")
            dates = [r["releasedAt"] for r in rows if r.get("releasedAt")]
            marks = {r["mark"] for r in rows if r.get("mark")}
            status = (
                "source-conflict"
                if conflicting
                else "unnumbered"
                if pid.startswith("CN:source:")
                else "article-missing"
                if not best.get("cardSource", {}).get("revision")
                else "listed"
            )
            notes = {
                "source-conflict": "52poke 的同一系列编号对应不同规则页，暂按独立来源条目保留，不能据此认定具体印刷或对战资格。",
                "unnumbered": "52poke 列出的无编号卡或尚未确定编号的来源条目；内部 ID 不是官方卡号。",
                "article-missing": "已从 52poke 产品卡表收录；独立卡牌文章缺失或暂不可获取，详细字段待补。",
                "listed": "已按 52poke 产品卡表及简中收录记录补齐资料；规则效果与赛制资格尚未审核。",
            }
            card = {
                **prior,
                **facts,
                "printingId": pid,
                "identityKind": "basic-energy"
                if candidate.startswith("CN:basic:")
                else "source-record"
                if pid.startswith("CN:source:")
                else "numbered-printing",
                "productCode": best.get("productCode"),
                "collectorNumber": best.get("collectorNumber"),
                "printedNumber": best["printedNumber"],
                "cnName": display_name,
                "cardPage": title,
                "region": "CN",
                "language": "zh-Hans",
                "category": category,
                "subtype": facts.get("subtype")
                or best.get("subtype")
                or prior.get("subtype"),
                "pokemonType": facts.get("pokemonType")
                or best.get("pokemonType")
                or prior.get("pokemonType")
                or "NONE",
                "isBasicPokemon": facts.get(
                    "isBasicPokemon", prior.get("isBasicPokemon")
                )
                if category == "宝可梦"
                else False,
                "hp": facts.get("hp", prior.get("hp")),
                "stage": facts.get("stage", prior.get("stage")),
                "evolvesFrom": facts.get("evolvesFrom", prior.get("evolvesFrom", [])),
                "basicEnergyType": basic,
                "aceSpec": bool(facts.get("aceSpec", prior.get("aceSpec", False))),
                "attacks": facts.get("attacks", prior.get("attacks", [])),
                "englishName": facts.get("englishName")
                or prior.get("englishName")
                or "",
                "mark": next(iter(marks))
                if len(marks) == 1
                else prior.get("mark")
                if not marks
                else None,
                "releasedAt": min(dates) if dates else prior.get("releasedAt"),
                "sourceVerified": False,
                "effectStatus": "unverified",
                "legacyReprintVerified": False,
                "engineId": "unverified:" + pid,
                "definitionId": "unverified:" + pid,
                "nameLimitKey": display_name,
                "images": prior.get("images", []),
                "aliases": sorted(
                    set(prior.get("aliases", []))
                    | {title, display_name, facts.get("englishName", "")} - {""}
                ),
                "sourceEvidence": sorted(set(prior.get("sourceEvidence", [])) | urls),
                "productIds": sorted(set(prior.get("productIds", [])) | set(links)),
                "catalogStatus": status,
                "reviewNote": notes[status],
            }
            if pid != candidate:
                card["claimedPrintingId"] = candidate
            card.pop("engineLine", None)
            cards[pid] = card
            for r in rows:
                assignments.append(
                    {
                        "printingId": pid,
                        "cardPage": title,
                        "evidence": r["sourceEvidence"],
                    }
                )
    result["cards"] = list(cards.values())
    result["asOf"] = inventory["asOf"]
    result.pop("version", None)
    result["version"] = digest(result)
    named_rows = {
        (e["source"]["pageId"], e["table"], e["row"])
        for a in assignments
        for e in a["evidence"]
        if e["method"] == "product-table"
    }
    source_rows = {
        (e["source"]["pageId"], e["table"], e["row"])
        for r in inventory["records"]
        for e in r["sourceEvidence"]
        if e["method"] == "product-table"
    }
    assert named_rows == source_rows
    report = {
        "schema": "p4-catalog-completion-v1",
        "asOf": inventory["asOf"],
        "catalogVersion": result["version"],
        "beforeCount": len(before),
        "afterCount": len(cards),
        "added": len(set(cards) - before),
        "supported": sum(c.get("effectStatus") == "verified" for c in cards.values()),
        "sourceProducts": len(inventory["products"]),
        "namedCNTableRows": len(source_rows),
        "mappedCNTableRows": len(named_rows),
        "unmappedNamedRows": 0,
        "excludedOtherRegionRows": len(inventory["excluded"]),
        "sourceStatusCounts": dict(
            Counter(
                c.get("catalogStatus", "existing-reviewed-or-listed")
                for c in cards.values()
            )
        ),
        "sourceEmptyTables": [
            {"product": p["title"], "code": t["code"], "table": t["table"]}
            for p in inventory["products"]
            for t in p["tables"]
            if not t["foreign"] and t["namedRows"] == 0
        ],
        "conflicts": conflicts,
        "assignments": assignments,
        "scope": "All named Simplified Chinese rows in the frozen 98 product pages, plus explicit CN expansion references in available card articles; not a claim that incomplete wiki pages enumerate every physical printing.",
    }
    return result, report


def publish(result, report):
    target = ROOT / "data/catalog/catalog.json"
    database = ROOT / "data/catalog/catalog.sqlite"
    backup = ROOT / ".catalog/cardpool/pre-catalog-completion"
    backup.mkdir(parents=True, exist_ok=True)
    if not (backup / "catalog.json").exists():
        (backup / "catalog.json").write_bytes(target.read_bytes())
        (backup / "catalog.sqlite").write_bytes(database.read_bytes())
    staged = database.with_suffix(".completion.sqlite")
    with (
        closing(sqlite3.connect(database)) as src,
        closing(sqlite3.connect(staged)) as dst,
    ):
        src.backup(dst)
        for p in result["products"]:
            dst.execute(
                "INSERT OR IGNORE INTO products VALUES(?,?,?,?,?,?)",
                (
                    p["id"],
                    p["name"],
                    p.get("releasedAt"),
                    p["source"]["url"],
                    p["source"].get("revision"),
                    p.get("tableCount"),
                ),
            )
        for c in result["cards"]:
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
            for p in c.get("productIds", []):
                dst.execute(
                    "INSERT OR IGNORE INTO card_products VALUES(?,?)",
                    (c["printingId"], p),
                )
        dst.execute(
            "CREATE TABLE IF NOT EXISTS p4_catalog_sources(printing_id TEXT,card_page TEXT,evidence_json TEXT)"
        )
        dst.execute("DELETE FROM p4_catalog_sources")
        dst.executemany(
            "INSERT INTO p4_catalog_sources VALUES(?,?,?)",
            [
                (
                    a["printingId"],
                    a["cardPage"],
                    json.dumps(a["evidence"], ensure_ascii=False),
                )
                for a in report["assignments"]
            ],
        )
        for key in ("version", "asOf"):
            dst.execute("UPDATE metadata SET value=? WHERE key=?", (result[key], key))
        dst.commit()
        assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not dst.execute("PRAGMA foreign_key_check").fetchall()
    staged.replace(database)
    temp = target.with_suffix(".completion.json")
    temp.write_bytes(encoded(result))
    temp.replace(target)
    (ROOT / "artifacts/cardpool/catalog-completion.json").write_bytes(encoded(report))


if __name__ == "__main__":
    inventory = read(ROOT / "artifacts/cardpool/catalog-inventory.json")
    result, report = augment(read(ROOT / "data/catalog/catalog.json"), inventory)
    report["inventorySha256"] = hashlib.sha256(
        (ROOT / "artifacts/cardpool/catalog-inventory.json").read_bytes()
    ).hexdigest()
    if "--publish" in sys.argv:
        publish(result, report)
    else:
        (ROOT / "artifacts/cardpool/catalog-completion-preview.json").write_bytes(
            encoded(report)
        )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("assignments", "conflicts")},
            ensure_ascii=False,
            indent=2,
        )
    )
