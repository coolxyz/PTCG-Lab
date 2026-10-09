"""Freeze the requested CN index and every linked product, resumably.

This is evidence acquisition, not card certification. Never substitutes an old
cache after a failed request, or treats missing release dates as released.
"""

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
from scripts.catalog.discover import discover  # noqa: E402

INDEX = "宝可梦集换式卡牌游戏列表"
OFFICIAL = "https://www.pokemon.cn/tcg-rules-regulation"


def fetch(url, cache, suffix):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + suffix)
    if path.exists():
        data = path.read_bytes()
    else:
        time.sleep(0.65)
        request = Request(
            url,
            headers={
                "User-Agent": "PTCG-local-P4-source-audit/1.0 (cached personal research)"
            },
        )
        with urlopen(request, timeout=40) as response:
            data = response.read()
        path.write_bytes(data)
    return data, {
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "cachePath": path.relative_to(ROOT).as_posix(),
    }


def wiki(title, cache):
    url = "https://wiki.52poke.com/api.php?" + urlencode(
        {"action": "parse", "page": title, "prop": "text|revid", "format": "json"}
    )
    data, source = fetch(url, cache, ".json")
    page = json.loads(data)["parse"]
    source.update(revision=page["revid"], pageId=page["pageid"], title=page["title"])
    return page, source


def metadata(html):
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    for row in soup.select("table tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) == 2 and cells[0].get_text(strip=True) in (
            "形式",
            "发布时间",
            "卡牌数目",
            "封装方式",
            "官方页面",
        ):
            result[cells[0].get_text(strip=True)] = cells[1].get_text(" ", strip=True)
    return result


def run(cutoff, output, refresh_id):
    date.fromisoformat(cutoff)
    # Explicit snapshot name separates a fresh freeze from the legacy cache.
    if not refresh_id.replace("-", "").isalnum():
        raise ValueError("INVALID_SNAPSHOT_NAME")
    cache = ROOT / ".catalog" / "cardpool" / refresh_id
    cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    page, source = wiki(INDEX, cache)
    products = discover(page["text"]["*"])
    data = {
        "schema": "p4-source-freeze-v1",
        "asOf": cutoff,
        "snapshot": refresh_id,
        "indexSource": source,
        "products": [],
        "errors": [],
    }
    try:
        _, official = fetch(OFFICIAL, cache, ".html")
        data["officialRulesSource"] = official
    except Exception as exc:
        data["errors"].append({"url": OFFICIAL, "error": str(exc)})
    for index, product in enumerate(products, 1):
        try:
            parsed, src = wiki(product["title"], cache)
            product.update(source=src, metadata=metadata(parsed["text"]["*"]))
        except Exception as exc:
            product["error"] = str(exc)
            data["errors"].append({"title": product["title"], "error": str(exc)})
        data["products"].append(product)
        data["fetchedAt"] = datetime.now(timezone.utc).isoformat()
        data["completeFetch"] = index == len(products) and not data["errors"]
        output.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"{index}/{len(products)} {product['title']} {'ERROR' if 'error' in product else 'ok'}",
            flush=True,
        )
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default="2026-10-01")
    parser.add_argument("--snapshot", default="20261001-v1")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/cardpool/source-index.json"
    )
    args = parser.parse_args()
    result = run(args.as_of, args.output, args.snapshot)
    raise SystemExit(0 if result["completeFetch"] else 1)
