"""Search official Traditional Chinese listings for unresolved wiki printings."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import sys
from urllib.parse import urlencode, urljoin
from scripts.catalog.complete_details import read, save, parse_page, traditional_candidates, page_assignments
from scripts.catalog.artwork import fetch, official_detail, set_key, match_detail, printed_key
from bs4 import BeautifulSoup
from opencc import OpenCC


def prefetch():
    source = read(".catalog/search-official.json")
    known = {**source.get("details", {}), **read(".catalog/details-official.json").get("details", {})}
    result = read(".catalog/search-prefetch.json")
    details, errors = result.get("details", {}), result.get("errors", {})
    urls = {u for v in source.get("searches", {}).values() for u in v} - known.keys() - details.keys()
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(official_detail, u): u for u in sorted(urls)}
        for n, job in enumerate(as_completed(jobs), 1):
            try: details[jobs[job]] = job.result()
            except Exception as e: errors[jobs[job]] = str(e)
            if n % 50 == 0 or n == len(jobs):
                save(".catalog/search-prefetch.json", {"details": details, "errors": errors})
                print("Prefetch", n, "/", len(jobs), "errors", len(errors), flush=True)


def search(query, broad=False):
    url = "https://asia.pokemon-card.com/tw/card-search/list/?" + urlencode(query)
    data, _ = fetch(url)
    soup = BeautifulSoup(data, "html.parser")
    links = {urljoin(url, a["href"]) for a in soup.select('a[href*="/card-search/detail/"]')}
    pages = re.search(r"共\s*(\d+)\s*頁", soup.get_text(" ", strip=True))
    if pages:
        for page in range(2, int(pages[1]) + 1):
            data, _ = fetch(url + "&pageNo=" + str(page))
            links.update(urljoin(url, a["href"]) for a in BeautifulSoup(data, "html.parser").select('a[href*="/card-search/detail/"]'))
    if not links and broad:
        name = "".join(re.findall(r"[\u3400-\u9fff]", query.get("keyword", "")))
        if len(name) > 2:
            for keyword in dict.fromkeys((name[:2], name[-2:])):
                links.update(search({**query, "keyword": keyword}))
                if links: break
    return sorted(links)


def import_expansion(code, region="hk"):
    """Use listing membership when shared expansion symbols lose deck identity."""
    base = f"https://asia.pokemon-card.com/{region}/card-search/list/?" + urlencode({"expansionCodes": code, "regulation": "all"})
    soup = BeautifulSoup(fetch(base + "&pageNo=1")[0], "html.parser")
    pages = re.search(r"共\s*(\d+)\s*頁", soup.get_text(" ", strip=True))
    links = {urljoin(base, a["href"]) for a in soup.select('a[href*="/card-search/detail/"]')}
    for page in range(2, int(pages[1]) + 1 if pages else 2):
        other = BeautifulSoup(fetch(base + "&pageNo=" + str(page))[0], "html.parser")
        links.update(urljoin(base, a["href"]) for a in other.select('a[href*="/card-search/detail/"]'))
    result = read("data/catalog/official-listings.json")
    details = result.setdefault("details", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        for detail in pool.map(official_detail, sorted(links)):
            details[detail["source"]] = {**detail, "symbolSetCode": detail["setCode"], "setCode": code, "listingSource": base}
    save("data/catalog/official-listings.json", result)
    print("Imported official listing", code, len(links), flush=True)


def main():
    if "--expansion" in sys.argv:
        import_expansion(sys.argv[sys.argv.index("--expansion") + 1])
        return
    cards = read("data/catalog/catalog.json")["cards"]
    pages = read(".catalog/details-pages.json")["pages"]
    official = read("data/catalog/official-artwork.json").get("details", {})
    official.update(read(".catalog/details-official.json").get("details", {}))
    old = read(".catalog/search-official.json")
    official.update(old.get("details", {}))
    official.update(read(".catalog/search-prefetch.json").get("details", {}))
    variants = read(".catalog/search-variants.json")
    official.update(variants.get("details", {}))
    broad_data = read(".catalog/search-broad.json")
    official.update(broad_data.get("details", {}))
    searches = {**old.get("searches", {}), **variants.get("searches", {}), **broad_data.get("searches", {})}
    broad = "--broad" in sys.argv
    attempts = set(broad_data.get("broadAttempts", []))
    output = ".catalog/search-broad.json" if broad else ".catalog/search-variants.json" if "--variants" in sys.argv else ".catalog/search-official.json"
    errors = {}
    conversion = OpenCC("s2tw")
    form = BeautifulSoup(fetch("https://asia.pokemon-card.com/tw/card-search/")[0], "html.parser")
    codes = {set_key(x["value"]): x["value"] for x in form.select("input.expansionCode[value]")}
    index = {}
    for d in official.values():
        if d.get("setCode"):
            index.setdefault((set_key(d["setCode"]), printed_key(d["printedNumber"])), []).append(d)
    rows = {title: parse_page(p.get("text"))[1] for title, p in pages.items()}
    assigned = page_assignments(cards, pages, {title: ([], r) for title, r in rows.items()})
    queries = {}
    for c in cards:
        if c.get("images"): continue
        candidates = traditional_candidates(rows.get(assigned[c["printingId"]], []))
        if any(any(match_detail(im, c, d) for d in index.get((set_key(im.get("setCode") or ""), printed_key(im.get("printedNumber"))), [])) for im in candidates):
            continue
        for im in candidates:
            code = codes.get(set_key(im.get("setCode") or ""))
            if not code: continue
            keyword = conversion.convert(c["cnName"]).replace("巖", "岩").replace("洛託姆", "洛托姆").replace("焰後蜥", "焰后蜥").replace("僞", "偽")
            keyword = re.sub(r"\s+(?:冠軍|亞軍|\d+強)$", "", keyword)
            q = {"expansionCodes": code, "regulation": "all", "keyword": keyword}
            key = urlencode(q)
            queries[key] = q
    todo = {key: q for key, q in queries.items() if key not in searches or (broad and not searches[key] and key not in attempts)}
    print("Official searches", len(queries), "pending", len(todo), flush=True)
    if "--plan" in sys.argv: return
    def checkpoint():
        save(output, {"details": official, "searches": searches, "errors": errors, "broadAttempts": sorted(attempts)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(search, q, broad): key for key, q in sorted(todo.items())}
        for n, job in enumerate(as_completed(jobs), 1):
            try:
                searches[jobs[job]] = job.result()
                if broad: attempts.add(jobs[job])
            except Exception as e: errors[jobs[job]] = str(e)
            if n % 50 == 0 or n == len(jobs):
                checkpoint(); print("Search", n, "/", len(jobs), "errors", len(errors), flush=True)
    urls = {u for key in queries for u in searches.get(key, [])}
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(official_detail, u): u for u in sorted(urls - official.keys())}
        for n, job in enumerate(as_completed(jobs), 1):
            try: official[jobs[job]] = job.result()
            except Exception as e: errors[jobs[job]] = str(e)
            if n % 50 == 0 or n == len(jobs):
                checkpoint(); print("Details", n, "/", len(jobs), "errors", len(errors), flush=True)
    checkpoint()


if __name__ == "__main__":
    prefetch() if "--prefetch" in sys.argv else main()
