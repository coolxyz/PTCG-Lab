"""Audit every cached product's numbered tables, including promos/precons.

Rows are evidence references, not certified printings: identical numbers on
different products/tables are intentionally not merged into rules identities.
No fetched data is promoted to the playable catalog by this command.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
from scripts.catalog.parse_sets import table_grid  # noqa: E402


def parse(html, product):
    soup = BeautifulSoup(html, "html.parser")
    rows, skipped, tables = [], [], 0
    for table_index, table in enumerate(soup.find_all("table")):
        direct = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if not direct:
            continue
        header = direct[0].find_all(["th", "td"], recursive=False)
        if (
            len(header) < 2
            or header[0].get_text(strip=True) != "编号"
            or "卡牌" not in header[1].get_text()
        ):
            continue
        for cell in table.find_all(["th", "td"]):
            for attribute in ("rowspan", "colspan"):
                if cell.get(attribute) == "":
                    cell[attribute] = "1"
        grid = table_grid(table)
        if not grid or len(grid[0]) < 2 or any(c is None for c in grid[0][:2]):
            continue
        head = [c.get_text(" ", strip=True) if c else "" for c in grid[0]]
        if head[0] != "编号" or "卡牌" not in head[1]:
            continue
        tables += 1
        for row_index, cells in enumerate(grid[1:], 2):
            ref = f"table-{table_index}:row-{row_index}"
            if len(cells) < 2 or any(c is None for c in cells[:2]):
                skipped.append({"ref": ref, "reason": "incomplete-row"})
                continue
            number, name = [c.get_text(" ", strip=True) for c in cells[:2]]
            if not re.match(r"^(?:\d+\s+)?(?:[A-Za-z]*\d+|[RGB])(?:/|\b)", number):
                skipped.append(
                    {
                        "ref": ref,
                        "reason": "non-card-or-unrecognized-number",
                        "text": number[:160],
                    }
                )
                continue
            link = cells[1].find("a", title=True)
            rows.append(
                {
                    "ref": ref,
                    "number": number,
                    "name": name,
                    "cardPage": link.get("title") if link else None,
                    "cardUrl": urljoin(product["url"], link["href"])
                    if link and link.get("href")
                    else None,
                    "sourceUrl": product["url"],
                    "sourceRevision": product.get("source", {}).get("revision"),
                    "status": "identity-and-format-review-required",
                }
            )
    return {"tables": tables, "rows": rows, "skipped": skipped}


def build():
    source_path = ROOT / "data/catalog/products-source.json"
    sources = json.loads(source_path.read_text(encoding="utf-8"))
    products = []
    for product in sources["products"]:
        source = product.get("source", {})
        path = ROOT / source.get("cachePath", "missing")
        row = {
            "title": product["title"],
            "url": product["url"],
            "metadata": product.get("metadata", {}),
        }
        if not path.is_file():
            row.update(status="missing-cache", rows=[], skipped=[], tables=0)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != source.get("sha256"):
            row.update(status="source-hash-mismatch", rows=[], skipped=[], tables=0)
        else:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            row.update(parse(parsed["parse"]["text"]["*"], product))
            row.update(
                status="extracted-unreviewed"
                if row["rows"]
                else "no-recognized-card-table",
                sourceHash=source["sha256"],
            )
        products.append(row)
    return {
        "schema": "p30-source-row-audit-v1",
        "asOf": sources["asOf"],
        "inputHash": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "targetCompleteness": "INCOMPLETE",
        "limitations": [
            "All cached index products examined; index completeness is not certified",
            "Rows include duplicate listings and may include future/non-standard cards",
            "No rule identity, legality or official-source verification inferred from a row",
            "Unrecognized rows remain in the report for review",
        ],
        "counts": {
            "products": len(products),
            "statuses": dict(Counter(p["status"] for p in products)),
            "tables": sum(p["tables"] for p in products),
            "evidenceRows": sum(len(p["rows"]) for p in products),
            "skippedRows": sum(len(p["skipped"]) for p in products),
        },
        "products": products,
    }


if __name__ == "__main__":
    report = build()
    path = ROOT / "artifacts/simulation/source-row-audit.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
