"""Discover only the index's Simplified-Chinese product navigation tables."""

import sys, json, re, urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.sources import ROOT, wiki
from bs4 import BeautifulSoup


def discover(html):
    soup = BeautifulSoup(html, "html.parser")
    products = {}
    for table in soup.find_all("table"):
        rows = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if len(rows) < 2 or "简体中文版" not in rows[0].get_text():
            continue
        series = rows[0].get_text(" ", strip=True)
        for row in rows[1:]:
            symbols = []
            for cell in row.find_all(["td", "th"], recursive=False):
                for a in cell.find_all("a"):
                    href = a.get("href", "")
                    title = a.get("title", "")
                    match = re.search(
                        r"SetSymbol([^/]+?)\.png", urllib.parse.unquote(href)
                    )
                    if match:
                        symbols.append(match[1])
                        continue
                    if (
                        not href.startswith("/wiki/")
                        or not title.endswith("（TCG）")
                        or "系列" in title
                    ):
                        continue
                    if title not in products:
                        products[title] = {
                            "title": title,
                            "name": a.get_text(" ", strip=True),
                            "url": urllib.parse.urljoin(
                                "https://wiki.52poke.com", href
                            ),
                            "series": series,
                            "indexSymbols": symbols.copy(),
                        }
                if cell.get_text(strip=True):
                    symbols = []
    return list(products.values())


if __name__ == "__main__":
    index, source = wiki("宝可梦集换式卡牌游戏列表")
    products = discover(index["text"]["*"])
    (ROOT / "data/catalog/product-index.json").write_text(
        json.dumps(
            {"source": source, "products": products}, ensure_ascii=False, indent=2
        )
        + "\n"
    )
    print("Discovered", len(products), "CN product pages", flush=True)
    result = []
    for product in products:
        try:
            page, src = wiki(product["title"])
            s = BeautifulSoup(page["text"]["*"], "html.parser")
            info = {}
            for tr in s.select("table tr"):
                cells = tr.find_all(["th", "td"], recursive=False)
                if len(cells) == 2 and cells[0].get_text(strip=True) in (
                    "形式",
                    "发布时间",
                    "卡牌数目",
                    "封装方式",
                    "官方页面",
                ):
                    info[cells[0].get_text(strip=True)] = cells[1].get_text(
                        " ", strip=True
                    )
            product.update(source=src, metadata=info)
            print(product["name"], info, flush=True)
        except Exception as e:
            product["error"] = str(e)
            print("ERROR", product["title"], str(e), flush=True)
        result.append(product)
        (ROOT / "data/catalog/products-source.json").write_text(
            json.dumps(
                {"asOf": "2026-09-30", "indexSource": source, "products": result},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
