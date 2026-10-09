"""Parse numbered CN booster tables without inferring battle admission."""

from pathlib import Path
import sys, json, re
from datetime import date
from urllib.parse import unquote, urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.sources import ROOT
from scripts.catalog.corrections import apply_number_correction
from bs4 import BeautifulSoup

AS_OF = "2026-09-30"
PACK_FORMS = {"补充包", "扩充包", "强化包", "专题包"}
ELEMENTS = {
    "草": "GRASS",
    "火": "FIRE",
    "水": "WATER",
    "雷": "LIGHTNING",
    "超": "PSYCHIC",
    "斗": "FIGHTING",
    "恶": "DARK",
    "鋼": "METAL",
    "钢": "METAL",
    "妖": "FAIRY",
    "龙": "DRAGON",
    "无色": "COLORLESS",
}
SUBTYPES = {
    "I": "ITEM",
    "Su": "SUPPORTER",
    "St": "STADIUM",
    "PT": "TOOL",
    "E": "ENERGY",
}


def clean_number(s):
    return re.sub(r"\s+", "-", s.strip())


def release_dates(text):
    return [
        date(*map(int, m)).isoformat()
        for m in re.findall(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    ]


def table_grid(table):
    pending = {}
    result = []
    rows = table.select(":scope > tbody > tr") or table.find_all("tr", recursive=False)
    for row in rows:
        cells = {col: cell for col, (cell, n) in pending.items()}
        pending = {col: (cell, n - 1) for col, (cell, n) in pending.items() if n > 1}
        col = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while col in cells:
                col += 1
            for _ in range(int(cell.get("colspan", 1))):
                cells[col] = cell
                if int(cell.get("rowspan", 1)) > 1:
                    pending[col] = (cell, int(cell["rowspan"]) - 1)
                col += 1
        result.append([cells.get(i) for i in range(max(cells, default=-1) + 1)])
    return result


def parse_tables(html, product):
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if len(rows) < 2:
            continue
        head = rows[0].find_all(["th", "td"], recursive=False)
        if (
            len(head) < 3
            or head[0].get_text(strip=True) != "编号"
            or "卡牌" not in head[1].get_text()
        ):
            continue
        symbol = table.find_previous("a", href=re.compile(r"SetSymbol[^/]+\.png"))
        code = (
            re.search(r"SetSymbol(.+?)\.png", unquote(symbol["href"]))[1]
            if symbol
            else None
        )
        if not code or not code.endswith("C"):
            continue
        records = []
        skipped = []
        for idx, cells in enumerate(table_grid(table)[1:], 2):
            if len(cells) < 3 or any(c is None for c in cells[:3]):
                continue
            number = cells[0].get_text(" ", strip=True)
            full_number = number
            match = re.match(r"(?:\d+\s+)?(?:[A-Za-z]*\d+|[RGB])/[\w-]+", number)
            if not match:
                if cells[1].find("a") and "/" in number:
                    skipped.append({"row": idx, "number": number})
                continue
            number = match[0]
            link = cells[1].find("a", title=True)
            name = (
                link.get_text(" ", strip=True)
                if link
                else cells[1].get_text(" ", strip=True)
            )
            if not name:
                skipped.append({"row": idx, "number": number, "reason": "no card name"})
                continue
            attributes = [i.get("alt", "") for i in cells[2].select("img")]
            kind = cells[2].get_text(" ", strip=True).split(" ")[0]
            category = (
                "能量" if kind == "E" else "训练家" if kind in SUBTYPES else "宝可梦"
            )
            records.append(
                {
                    "collectorNumber": clean_number(number.split("/")[0]),
                    "printedNumber": number,
                    "numberLabel": full_number,
                    "cnName": name,
                    "cardPage": re.sub(r"（页面不存在）$", "", link.get("title", ""))
                    if link
                    else None,
                    "cardUrl": urljoin("https://wiki.52poke.com", link["href"])
                    if link
                    else None,
                    "category": category,
                    "subtype": SUBTYPES.get(kind),
                    "pokemonType": next(
                        (ELEMENTS[a] for a in attributes if a in ELEMENTS), None
                    ),
                    "rarity": cells[3].get_text(" ", strip=True)
                    if len(cells) > 3
                    else None,
                    "sourceRow": idx,
                }
            )
        tables.append({"code": code, "records": records, "unparsedRows": skipped})
    return tables


def main():
    source = json.loads((ROOT / "data/catalog/products-source.json").read_text())
    override_path = ROOT / "data/catalog/number-overrides.json"
    corrections = (
        json.loads(override_path.read_text())["corrections"]
        if override_path.exists()
        else []
    )
    applied = []
    products = []
    cards = {}
    issues = []
    conflicts = {}
    source_rows = []
    for p in source["products"]:
        info = p.get("metadata", {})
        dates = release_dates(info.get("发布时间", ""))
        status = (
            "source_error"
            if "error" in p
            else "excluded_non_pack"
            if info.get("形式") not in PACK_FORMS
            else "unknown_release"
            if not dates
            else "not_released"
            if min(dates) > AS_OF
            else "released"
        )
        if status != "released":
            products.append({**p, "status": status})
            continue
        parsed = json.loads((ROOT / p["source"]["cachePath"]).read_text())["parse"]
        tables = parse_tables(parsed["text"]["*"], p)
        if not tables:
            issues.append({"product": p["title"], "code": "NO_CARD_TABLE"})
        for i, table in enumerate(tables):
            code = table["code"]
            released = dates[i] if len(dates) == len(tables) else min(dates)
            if not code:
                issues.append({"product": p["title"], "code": "MISSING_SET_CODE"})
                continue
            product = {
                **p,
                "id": code + ":" + str(parsed["pageid"]) + ":" + str(i),
                "name": p["name"] if len(tables) == 1 else p["name"] + " · " + code,
                "releasedAt": released,
                "status": "released" if released <= AS_OF else "not_released",
                "tableCount": len(table["records"]),
                "unparsedRows": table["unparsedRows"],
            }
            products.append(product)
            if released > AS_OF:
                continue
            for r in table["records"]:
                pid = f"CN:{code}:{r['collectorNumber']}"
                source_rows.append(
                    {
                        **r,
                        "printingId": pid,
                        "productId": product["id"],
                        "source": p["source"],
                    }
                )
                r = apply_number_correction(
                    r, code, p["source"]["revision"], corrections
                )
                pid = f"CN:{code}:{r['collectorNumber']}"
                if r.get("numberCorrection"):
                    applied.append(r["numberCorrection"])
                card = {
                    **r,
                    "printingId": pid,
                    "productCode": code,
                    "productIds": [product["id"]],
                    "productName": product["name"],
                    "releasedAt": released,
                    "source": p["source"],
                    "sourceVerified": False,
                    "effectStatus": "unverified",
                    "region": "CN",
                    "language": "zh-Hans",
                    "images": [],
                }
                if pid in cards and cards[pid]["cardPage"] != r["cardPage"]:
                    issues.append(
                        {
                            "code": "PRINTING_COLLISION",
                            "printingId": pid,
                            "names": [cards[pid]["cnName"], r["cnName"]],
                        }
                    )
                    conflicts.setdefault(pid, [cards[pid]]).append(card)
                    continue
                if pid in cards:
                    cards[pid]["productIds"] = list(
                        dict.fromkeys(cards[pid]["productIds"] + [product["id"]])
                    )
                    cards[pid]["releasedAt"] = min(cards[pid]["releasedAt"], released)
                else:
                    cards[pid] = card
    for pid in conflicts:
        cards.pop(pid, None)
    if {c["id"] for c in applied} != {c["id"] for c in corrections}:
        raise ValueError("Some number corrections did not match source rows")
    out = {
        "corrections": applied,
        "sourceRows": source_rows,
        "conflicts": conflicts,
        "schema": "cn-pool-v1",
        "asOf": AS_OF,
        "products": products,
        "cards": list(cards.values()),
        "issues": issues,
    }
    (ROOT / "data/catalog/card-tables.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "Products",
        len(products),
        "released",
        sum(p["status"] == "released" for p in products),
        "cards",
        len(cards),
        "unique card pages",
        len({c["cardPage"] for c in cards.values()}),
        "issues",
        len(issues),
    )
    for issue in issues:
        print(issue)


if __name__ == "__main__":
    main()
