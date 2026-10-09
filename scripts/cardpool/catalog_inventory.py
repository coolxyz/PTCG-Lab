"""Account for every named card row, including decks and unnumbered prizes.

Catalogue inclusion is deliberately independent of format/engine certification.
"""

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
from scripts.catalog.parse_sets import table_grid, release_dates, ELEMENTS, SUBTYPES  # noqa: E402
from scripts.catalog.enrich import parse_page, number  # noqa: E402
from scripts.catalog.corrections import apply_number_correction  # noqa: E402

PROMOS = ("SM-P", "S-P", "SV-P", "30th-P", "M-P")
ENERGIES = {
    "GRA": "grass",
    "FIR": "fire",
    "WAT": "water",
    "LIG": "lightning",
    "PSY": "psychic",
    "FIG": "fighting",
    "DAR": "darkness",
    "MET": "metal",
    "FAI": "fairy",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cn_code(code):
    return bool(code and (re.fullmatch(r"[A-Za-z0-9.]+C", code) or code in PROMOS))


def symbol(link):
    match = re.search(r"SetSymbol([^/?&]+)\.png", unquote(link.get("href", "")))
    return match[1] if match else None


def sections(node):
    active = {}
    for h in reversed(list(node.find_all_previous(["h2", "h3", "h4"]))):
        level = int(h.name[1])
        active = {k: v for k, v in active.items() if k < level}
        active[level] = h.get_text(" ", strip=True).split("[")[0].strip()
    return list(active.values())


def product_tables(html, title):
    soup = BeautifulSoup(html, "html.parser")
    # Navigation symbols describe other products and must never assign a set.
    for node in soup.select(".navbox, .catlinks"):
        node.decompose()
    codes = sorted(
        {symbol(a) for a in soup.find_all("a", href=True) if cn_code(symbol(a))}
    )
    promo = next((p for p in PROMOS if title.startswith(p + "简体")), None)
    if promo:
        codes = [promo]
    records, skipped, summaries = [], [], []
    for ti, table in enumerate(soup.find_all("table")):
        direct = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if not direct:
            continue
        head = direct[0].find_all(["th", "td"], recursive=False)
        if (
            len(head) < 2
            or head[0].get_text(strip=True) != "编号"
            or "卡牌" not in head[1].get_text()
        ):
            continue
        for cell in table.find_all(["td", "th"]):
            for attr in ("rowspan", "colspan"):
                if not cell.get(attr, "1").isdigit():
                    cell[attr] = "1"
        headings = sections(table)
        foreign = any(
            re.search(r"韩文|韓文|英文版|泰印尼|泰文版|印尼文", h)
            and not re.search(r"中|简", h)
            for h in headings
        )
        preceding = [
            symbol(a) for a in table.find_all_previous("a", href=True) if symbol(a)
        ]
        display_code = preceding[0] if preceding else None
        code = promo or next((c for c in preceding if cn_code(c)), None)
        summary = {
            "table": ti,
            "headings": headings,
            "displayCode": display_code,
            "code": code,
            "foreign": foreign,
            "namedRows": 0,
        }
        summaries.append(summary)
        for ri, cells in enumerate(table_grid(table)[1:], 2):
            if len(cells) < 2 or cells[0] is None or cells[1] is None:
                skipped.append({"table": ti, "row": ri, "reason": "layout-row"})
                continue
            label = cells[0].get_text(" ", strip=True)
            a = cells[1].find("a", title=True)
            name = cells[1].get_text(" ", strip=True)
            if not a or not name or label == "编号":
                skipped.append(
                    {
                        "table": ti,
                        "row": ri,
                        "reason": "source-placeholder"
                        if not name or name in ("—", "?", "???")
                        else "non-card-row",
                        "text": " | ".join(
                            c.get_text(" ", strip=True) for c in cells if c
                        )[:200],
                    }
                )
                continue
            summary["namedRows"] += 1
            numbered = re.match(r"(?:\d+\s+)?(?:[A-Za-z]*\d+|[RGB])/([\w-]+)", label)
            row_code = (
                numbered[1]
                if numbered and numbered[1] in PROMOS
                else label
                if label in PROMOS
                else code
            )
            attrs = (
                cells[2].get_text(" ", strip=True)
                if len(cells) > 2 and cells[2]
                else ""
            )
            image = cells[2].find("img") if len(cells) > 2 and cells[2] else None
            element = ELEMENTS.get(image.get("alt")) if image else None
            is_energy = label in ENERGIES or attrs == "E"
            records.append(
                {
                    "table": ti,
                    "row": ri,
                    "sourceRow": ri,
                    "foreign": foreign,
                    "headings": headings,
                    "displayCode": display_code,
                    "productCode": row_code,
                    "collectorNumber": number(label) if numbered else None,
                    "printedNumber": label,
                    "cnName": name,
                    "cardPage": a["title"].removesuffix("（页面不存在）"),
                    "category": "能量"
                    if is_energy
                    else "训练家"
                    if attrs in SUBTYPES
                    else "宝可梦"
                    if element
                    else "未知",
                    "subtype": SUBTYPES.get(attrs),
                    "pokemonType": element,
                    "basicEnergyType": ENERGIES.get(label),
                    "availabilityText": " | ".join(
                        c.get_text(" ", strip=True) for c in cells[2:] if c
                    ),
                }
            )
    return records, {"codes": codes, "tables": summaries, "skipped": skipped}


def load_articles():
    pages = {}
    for name in (
        ".catalog/card-pages.json",
        ".catalog/cardpool/card-pages.json",
        ".catalog/cardpool/supplement-articles.json",
        ".catalog/cardpool/html-articles.json",
        ".catalog/cardpool/refreshed-articles.json",
    ):
        path = ROOT / name
        if path.exists():
            pages.update(read(path)["pages"])
    return pages


def build(source, pages):
    parsed = {}
    for title, page in pages.items():
        canonical = page.get("title", title)
        if canonical not in parsed:
            parsed[canonical] = (
                (page["facts"], page["paired"], page)
                if "facts" in page
                else (*parse_page(page.get("text")), page)
            )
    corrections = read(ROOT / "data/catalog/number-overrides.json")["corrections"]
    products, records, excluded, issues = [], [], [], []
    by_name, by_code = {}, defaultdict(list)
    for product in source["products"]:
        src = product["source"]
        raw = (ROOT / src["cachePath"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != src["sha256"]:
            raise ValueError("SOURCE_CHANGED: " + src["cachePath"])
        html = json.loads(raw)["parse"]["text"]["*"]
        rows, summary = product_tables(html, product["title"])
        dates = release_dates(product.get("metadata", {}).get("发布时间", ""))
        release = max(dates) if dates else None
        p = {
            "id": f"wiki:{src['pageId']}",
            "title": product["title"],
            "name": product["title"].removesuffix("（TCG）"),
            "releasedAt": release,
            "source": src,
            **summary,
        }
        products.append(p)
        by_name[p["name"].replace("_", " ")] = p
        for code in p["codes"]:
            by_code[code].append(p)
        for row in rows:
            evidence = {
                "productId": p["id"],
                "product": p["title"],
                "source": src,
                "table": row["table"],
                "row": row["row"],
                "method": "product-table",
            }
            if row["foreign"]:
                excluded.append(
                    {
                        **row,
                        "evidence": evidence,
                        "reason": "explicit-other-region-section",
                    }
                )
                continue
            if row["collectorNumber"] and row["productCode"]:
                try:
                    row = apply_number_correction(
                        row, row["productCode"], src["revision"], corrections
                    )
                except ValueError as exc:
                    issues.append(
                        {
                            "reason": "stale-number-correction",
                            "evidence": evidence,
                            "error": str(exc),
                        }
                    )
            canonical = pages.get(row["cardPage"], {}).get("title", row["cardPage"])
            facts, paired, page = parsed.get(canonical, ({}, [], {}))
            exact = [
                r
                for r in paired
                if r.get("cnicon") == row["productCode"]
                and row["collectorNumber"]
                and number(r.get("cnno", "")) == row["collectorNumber"]
            ]
            marks = {r["reg"] for r in exact if r.get("reg")}
            times = {
                r["cntime"]
                for r in exact
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.get("cntime", ""))
            }
            row.update(
                cardPage=canonical,
                facts=facts,
                cardSource={
                    k: v
                    for k, v in page.items()
                    if k not in ("text", "facts", "paired")
                },
                mark=next(iter(marks)) if len(marks) == 1 else None,
                releasedAt=min(times) if times else release,
                sourceEvidence=[evidence],
                articlePaired=bool(exact),
            )
            records.append(row)
    # Reverse cross-reference recovers cards omitted from incomplete product
    # tables. The article must explicitly name a known CN product or set code.
    for title, (facts, paired, page) in parsed.items():
        for row in paired:
            code = row.get("cnicon")
            if not cn_code(code):
                continue
            product = by_name.get(row.get("cnexpansion", "").replace("_", " "))
            parents = [product] if product else by_code.get(code, [])
            collector = number(row.get("cnno", ""))
            if not parents or not collector:
                continue
            # A shared series code is not proof of inclusion in every pack.
            # Keep the printing, but with series-only evidence in this case.
            if len(parents) > 1:
                parents = []
            date = row.get("cntime", "")
            records.append(
                {
                    "productCode": code,
                    "collectorNumber": collector,
                    "printedNumber": row["cnno"],
                    "cnName": title.split("（")[0],
                    "cardPage": title,
                    "facts": facts,
                    "category": facts.get("category", "未知"),
                    "subtype": facts.get("subtype"),
                    "pokemonType": facts.get("pokemonType"),
                    "basicEnergyType": facts.get("basicEnergyType"),
                    "mark": row.get("reg"),
                    "articlePaired": True,
                    "releasedAt": date
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
                    else max((p["releasedAt"] or "" for p in parents), default="")
                    or None,
                    "cardSource": {
                        k: v
                        for k, v in page.items()
                        if k not in ("text", "facts", "paired")
                    },
                    "sourceEvidence": [
                        {
                            "productId": p["id"],
                            "product": p["title"],
                            "source": p["source"],
                            "method": "card-article-cn-expansion",
                            "cnicon": code,
                            "cnno": row["cnno"],
                            "cntime": date,
                        }
                        for p in parents
                    ]
                    or [
                        {
                            "productId": None,
                            "product": None,
                            "source": {
                                k: v
                                for k, v in page.items()
                                if k not in ("text", "facts", "paired")
                            },
                            "method": "card-article-cn-series",
                            "cnicon": code,
                            "cnno": row["cnno"],
                            "cntime": date,
                        }
                    ],
                }
            )
    return {
        "schema": "p4-catalog-inventory-v1",
        "asOf": source["asOf"],
        "products": products,
        "records": records,
        "excluded": excluded,
        "issues": issues,
    }


if __name__ == "__main__":
    result = build(read(ROOT / "data/cardpool/source-index.json"), load_articles())
    dest = ROOT / "artifacts/cardpool/catalog-inventory.json"
    dest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "records": len(result["records"]),
                "excluded": len(result["excluded"]),
                "issues": result["issues"],
            },
            ensure_ascii=False,
        )
    )
