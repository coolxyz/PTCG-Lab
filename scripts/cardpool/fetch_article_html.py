"""Read public card HTML when MediaWiki revision queries are unavailable."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
from scripts.catalog.parse_sets import table_grid  # noqa: E402
from scripts.catalog.enrich import ELEMENTS, STAGES, number  # noqa: E402
from scripts.cardpool.catalog_inventory import cn_code, symbol  # noqa: E402


def parse_html(html):
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one(".mw-parser-output")
    if not content:
        raise ValueError("CARD_CONTENT_MISSING")
    title = soup.select_one("#firstHeading").get_text(" ", strip=True)
    rev = re.search(r"[?&]oldid=(\d+)", html) or re.search(
        r'"wgRevisionId":(\d+)', html
    )
    facts, paired = {}, []
    intro = content.find("p")
    intro = intro.get_text(" ", strip=True) if intro else ""
    english = re.search(r"英文[︰：:]\s*([^）]+)", intro)
    if english:
        facts["englishName"] = english[1].strip()
    tables = content.find_all("table")
    primary = next((t for t in tables if "HP" in t.get_text()), None)
    trainer_intro = any(
        s in intro
        for s in (
            "训练家卡",
            "訓練家卡",
            "支援者",
            "物品卡",
            "竞技场",
            "競技場",
            "宝可梦道具",
            "寶可夢道具",
        )
    )
    energy_intro = "能量卡" in intro
    if primary and not trainer_intro and not energy_intro and "宝可梦" in intro:
        header = next(
            (
                t
                for t in primary.find_all("table")
                if "HP" in t.get_text() and len(t.get_text()) < 600
            ),
            primary,
        )
        text = header.get_text(" ", strip=True)
        hp = re.search(r"HP\s*(\d+)", text)
        stage = next((v for k, v in STAGES.items() if text.startswith(k)), None)
        element = next(
            (
                ELEMENTS[i.get("alt")]
                for i in header.find_all("img")
                if i.get("alt") in ELEMENTS
            ),
            None,
        )
        facts.update(
            category="宝可梦",
            hp=int(hp[1]) if hp else None,
            stage=stage,
            isBasicPokemon=stage == "BASIC" if stage else None,
            pokemonType=element,
        )
    elif trainer_intro:
        subtype = next(
            (
                v
                for k, v in {
                    "支援者": "SUPPORTER",
                    "物品卡": "ITEM",
                    "竞技场": "STADIUM",
                    "競技場": "STADIUM",
                    "宝可梦道具": "TOOL",
                    "寶可夢道具": "TOOL",
                }.items()
                if k in intro
            ),
            None,
        )
        facts.update(category="训练家", subtype=subtype)
    elif energy_intro:
        facts["category"] = "能量"
    # Each expansion row has seven CN columns, seven Traditional Chinese
    # columns, then the regulation column. Never borrow the other locale's ID.
    for table in tables:
        direct = table.select(":scope > tbody > tr") or table.find_all(
            "tr", recursive=False
        )
        if not direct or direct[0].get_text(strip=True) != "中文卡包":
            continue
        for cell in table.find_all(["th", "td"]):
            for attr in ("rowspan", "colspan"):
                if not cell.get(attr, "1").isdigit():
                    cell[attr] = "1"
        for cells in table_grid(table):
            if len(cells) < 15 or any(cells[i] is None for i in (0, 1, 3, 5)):
                continue
            code = next(
                (
                    symbol(a)
                    for a in cells[0].find_all("a", href=True)
                    if cn_code(symbol(a))
                ),
                None,
            )
            printed = cells[5].get_text(" ", strip=True)
            if not code or not number(printed):
                continue
            reg = (
                next(
                    (
                        i.get("alt")
                        for i in cells[14].find_all("img")
                        if i.get("alt") in tuple("ABCDEFGHIJ")
                    ),
                    None,
                )
                if cells[14]
                else None
            )
            paired.append(
                {
                    "cnicon": code,
                    "cnexpansion": cells[1].get_text(" ", strip=True),
                    "cntime": cells[3].get_text(" ", strip=True),
                    "cnno": printed,
                    "reg": reg,
                    "images": [],
                }
            )
    return {
        "title": title,
        "revision": int(rev[1]) if rev else None,
        "facts": facts,
        "paired": paired,
        "metadataMethod": "public-card-html",
    }


def fetch(title):
    url = "https://wiki.52poke.com/wiki/" + quote(title)
    cache = (
        ROOT
        / ".catalog/cardpool/html-articles"
        / (hashlib.sha256(url.encode()).hexdigest() + ".html")
    )
    if cache.exists():
        raw = cache.read_bytes()
    else:
        time.sleep(0.6)
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "PTCG-local-catalog/0.1"}),
                timeout=8,
            ) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return {
                    "title": title,
                    "revision": None,
                    "sourceUrl": url,
                    "unavailable": "HTTP 404",
                }
            raise
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    result = parse_html(raw.decode("utf-8"))
    result.update(
        sourceUrl=url,
        responseSha256=hashlib.sha256(raw).hexdigest(),
        cachePath=cache.relative_to(ROOT).as_posix(),
    )
    return result


if __name__ == "__main__":
    inventory = json.loads(
        (ROOT / "artifacts/cardpool/catalog-inventory.json").read_text(encoding="utf-8")
    )
    output = ROOT / ".catalog/cardpool/html-articles.json"
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    pages = previous.get("pages", {})
    errors = previous.get("errors", [])
    # Refresh parsing locally, without re-requesting successfully cached pages.
    for title, page in list(pages.items()):
        if page.get("cachePath"):
            parsed = parse_html((ROOT / page["cachePath"]).read_text(encoding="utf-8"))
            pages[title] = {**page, **parsed}
    attempted = {e["title"] for e in errors}
    titles = sorted(
        {
            r["cardPage"]
            for r in inventory["records"]
            if not r.get("cardSource", {}).get("revision")
        }
        - pages.keys()
        - attempted
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(fetch, title): title for title in titles}
        for i, job in enumerate(as_completed(jobs), 1):
            try:
                pages[jobs[job]] = job.result()
            except Exception as exc:
                errors.append({"title": jobs[job], "error": str(exc)})
            if i % 10 == 0 or i == len(jobs):
                output.write_text(
                    json.dumps(
                        {"pages": pages, "errors": errors}, ensure_ascii=False, indent=2
                    ),
                    encoding="utf-8",
                )
                print(f"{i}/{len(jobs)} articles, {len(errors)} errors", flush=True)
