"""Resolve only observed official source URLs; cache verifiable card artwork.

Wiki file descriptions are discovery evidence, not licenses. Matching artwork never
certifies a rules implementation. No guessed image addresses or offsite proxies.
"""

from pathlib import Path
import sys, json, re, hashlib, urllib.parse, argparse, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bs4 import BeautifulSoup
from scripts.catalog.sources import ROOT, retrieve, CACHE

OUT = ROOT / "data/catalog/official-artwork.json"
LOCK = threading.Lock()
NEXT = 0.0


def fetch(url, kind="html"):
    global NEXT
    cached = CACHE / (hashlib.sha256(url.encode()).hexdigest() + "." + kind)
    if not cached.exists():
        with LOCK:
            wait = max(0, NEXT - time.monotonic())
            NEXT = max(time.monotonic(), NEXT) + 0.4
        if wait:
            time.sleep(wait)
    return retrieve(url, kind)


def origins(text):
    urls = re.findall(r"https?://[^\s\]|}<>]+", text or "")
    return list(
        dict.fromkeys(
            u.split("#")[0]
            for u in urls
            if re.fullmatch(
                r"https://asia\.pokemon-card\.com/(?:hk|tw)/card-search/detail/\d+/?", u
            )
            or urllib.parse.urlparse(u).hostname
            in ("www.pokemon.cn", "special.pokemon.cn")
        )
    )


def official_detail(url):
    data, path = fetch(url)
    raw = data.decode("utf-8")
    start = raw.find('<div class="cardDetailPage">')
    end = raw.find('<section class="extraInformationColumn">', start)
    header = re.search(r"<h1\b[^>]*>.*?</h1>", raw, re.S)
    # Search forms and navigation dominate these pages; parse the observed
    # card identity block only, retaining full source bytes as evidence.
    fragment = (header[0] if header else "") + raw[start:end] if start >= 0 and end > start else raw
    s = BeautifulSoup(fragment, "html.parser")
    im = s.select_one(".cardImage img")
    num = s.select_one(".collectorNumber")
    symbol = s.select_one(".expansionSymbol img")
    h = s.select_one("h1")
    if not im or not num or not symbol:
        raise ValueError("官方页面缺少卡图、编号或系列字段")
    image = urllib.parse.urljoin(url, im.get("src", ""))
    if not re.fullmatch(
        r"https://asia\.pokemon-card\.com/(?:hk|tw)/card-img/(?:hk|tw)\d+\.(?:png|jpg)",
        image,
    ):
        raise ValueError("不符合官网卡图地址格式")
    body = s.get_text(" ", strip=True)
    hp = re.search(r"HP\s*(\d+)", body)
    return {
        "source": url,
        "url": image,
        "locale": "zh-Hant",
        "printedNumber": num.get_text(strip=True),
        "setCode": Path(symbol.get("src", "")).stem,
        "name": h.get_text(" ", strip=True) if h else "",
        "hp": int(hp[1]) if hp else None,
        "sourceSha256": hashlib.sha256(data).hexdigest(),
        "cachePath": str(path.relative_to(ROOT)),
    }


def cn_page(url):
    data, path = fetch(url)
    s = BeautifulSoup(data, "html.parser")
    return {
        "source": url,
        "locale": "zh-Hans",
        "images": [
            {
                "url": urllib.parse.urljoin(url, i.get("src", "")),
                "alt": i.get("alt", ""),
            }
            for i in s.select("img[src]")
        ],
        "sourceSha256": hashlib.sha256(data).hexdigest(),
        "cachePath": str(path.relative_to(ROOT)),
    }


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def set_key(value):
    # Observed official symbol filenames include design-tool export affixes.
    # F denotes the Traditional-Chinese print locale; the source page is HK/TW.
    value = value.lower()
    value = re.sub(r"@\d+x$", "", value)
    value = re.sub(
        r"^(?:s_mark_expantion_|sm_expantion_(?:mark_)?|expansion_mark_)", "", value
    )
    value = re.sub(r"(?:^|_)(?:twhk|hk|tw|exp|ex|ol)(?=_|$)", "_", value)
    value = re.sub(r"out$", "", value)
    return norm(value).removesuffix("f")


def printed_key(value):
    return re.sub(r"(?<![A-Za-z0-9])0+(?=\d)", "", re.sub(r"\s+", "", value or ""))


def match_detail(candidate, card, detail):
    return (
        set_key(candidate.get("setCode") or "") == set_key(detail["setCode"])
        and printed_key(candidate.get("printedNumber")) == printed_key(detail["printedNumber"])
        and (not card.get("hp") or card["hp"] == detail["hp"])
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Rebuild mappings using cached details only",
    )
    args = parser.parse_args()
    cards = json.loads((ROOT / "data/catalog/enriched.json").read_text())["cards"]
    pages = json.loads((ROOT / ".catalog/image-pages.json").read_text())["pages"]
    file_origins = {k: origins(p.get("text")) for k, p in pages.items()}
    urls = sorted(
        {
            u
            for c in cards
            for i in c.get("imageCandidates", [])
            for u in file_origins.get("File:" + i["file"], [])
        }
    )
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    details = old.get("details", {})
    errors = old.get("errors", {}) if args.offline else {}
    todo = [] if args.offline else [u for u in urls if u not in details]

    def save():
        if args.offline:
            return
        OUT.write_text(
            json.dumps(
                {"details": details, "errors": errors}, ensure_ascii=False, indent=2
            )
            + "\n"
        )

    print(
        "Official sources",
        len(urls),
        "cached",
        len(details),
        "pending",
        len(todo),
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=10) as pool:
        jobs = {
            pool.submit(
                official_detail if "asia.pokemon-card.com" in u else cn_page, u
            ): u
            for u in todo
        }
        for n, j in enumerate(as_completed(jobs), 1):
            try:
                details[jobs[j]] = j.result()
            except Exception as e:
                errors[jobs[j]] = str(e)
            if n % 50 == 0 or n == len(todo):
                save()
                print(n, "/", len(todo), "sources;", len(errors), "errors", flush=True)
    save()
    mappings = {}
    gaps = {}
    for c in cards:
        choices = []
        rejected = []
        for im in c.get("imageCandidates", []):
            for url in file_origins.get("File:" + im["file"], []):
                d = details.get(url)
                if not d:
                    continue
                if d["locale"] == "zh-Hant" and im["locale"] == "zh-Hant":
                    if match_detail(im, c, d):
                        choices.append(
                            {
                                **d,
                                "fileEvidence": pages["File:" + im["file"]][
                                    "sourceUrl"
                                ],
                                "fileRevision": pages["File:" + im["file"]]["revision"],
                                "matchMethod": "paired-row+official-set-number-hp",
                                "equivalenceReviewed": True,
                            }
                        )
                    else:
                        rejected.append(
                            {
                                "file": im["file"],
                                "source": url,
                                "reason": "OFFICIAL_IDENTITY_MISMATCH",
                            }
                        )
                if d["locale"] == "zh-Hans" and im["locale"] == "zh-Hans":
                    # Accept only a unique literal card filename/alt; no positional guessing.
                    expected = norm(Path(im["file"]).stem)
                    matches = [
                        a
                        for a in d["images"]
                        if norm(
                            Path(
                                urllib.parse.unquote(
                                    urllib.parse.urlparse(a["url"]).path
                                )
                            ).stem
                        )
                        == expected
                        or norm(Path(a["alt"]).stem) == expected
                    ]
                    matches = [
                        a
                        for a in matches
                        if urllib.parse.urlparse(a["url"]).hostname
                        in ("www.pokemon.cn", "special.pokemon.cn")
                        and re.search(r"\.(png|jpg|jpeg|webp)$", a["url"], re.I)
                    ]
                    if len({a["url"] for a in matches}) == 1:
                        choices.append(
                            {
                                "source": url,
                                "url": matches[0]["url"],
                                "locale": "zh-Hans",
                                "fileEvidence": pages["File:" + im["file"]][
                                    "sourceUrl"
                                ],
                                "fileRevision": pages["File:" + im["file"]]["revision"],
                                "matchMethod": "paired-row+official-literal-filename",
                                "equivalenceReviewed": True,
                                "sourceSha256": d["sourceSha256"],
                            }
                        )
        choices.sort(key=lambda d: d["locale"] != "zh-Hans")
        if choices:
            mappings[c["printingId"]] = choices[0]
        else:
            source_urls = [
                u
                for im in c.get("imageCandidates", [])
                for u in file_origins.get("File:" + im["file"], [])
            ]
            reason = (
                "NO_IMAGE_CANDIDATE"
                if not c.get("imageCandidates")
                else "NO_OFFICIAL_REFERENCE"
                if not source_urls
                else "SOURCE_UNAVAILABLE"
                if not any(u in details for u in source_urls)
                else "NO_VERIFIABLE_OFFICIAL_IMAGE"
            )
            gaps[c["printingId"]] = {
                "reason": "IDENTITY_MISMATCH" if rejected else reason,
                "rejected": rejected,
            }
    cn_path = ROOT / "data/catalog/cn151-images.json"
    if cn_path.exists():
        mappings.update(json.loads(cn_path.read_text())["mappings"])
        for pid in mappings:
            gaps.pop(pid, None)
    previous_path = ROOT / "data/catalog/image-mappings.json"
    previous = (
        json.loads(previous_path.read_text()).get("mappings", {})
        if previous_path.exists()
        else {}
    )
    for pid, item in mappings.items():
        old_item = previous.get(pid, {})
        local_path = old_item.get("localPath")
        if (
            old_item.get("url") == item["url"]
            and local_path
            and (ROOT / local_path).is_file()
        ):
            if hashlib.sha256(
                (ROOT / local_path).read_bytes()
            ).hexdigest() == old_item.get("sha256"):
                item.update(
                    {
                        k: old_item[k]
                        for k in ("sha256", "localUrl", "localPath", "bytes")
                        if k in old_item
                    }
                )
    if args.download:
        assets = {}

        def download(url):
            b, p = fetch(url, "img")
            if not (
                b.startswith(b"\x89PNG\r\n\x1a\n")
                or b.startswith(b"\xff\xd8")
                or b[:4] == b"RIFF"
            ):
                raise ValueError("非图片响应")
            sha = hashlib.sha256(b).hexdigest()
            suffix = (
                ".png"
                if b.startswith(b"\x89PNG")
                else ".jpg"
                if b.startswith(b"\xff\xd8")
                else ".webp"
            )
            dest = ROOT / ".catalog/images" / (sha + suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b)
            return {
                "sha256": sha,
                "bytes": len(b),
                "localUrl": "/card-images/" + dest.name,
                "localPath": str(dest.relative_to(ROOT)),
            }

        with ThreadPoolExecutor(max_workers=10) as pool:
            jobs = {
                pool.submit(download, u): u
                for u in sorted({m["url"] for m in mappings.values()})
            }
            for n, j in enumerate(as_completed(jobs), 1):
                try:
                    assets[jobs[j]] = j.result()
                except Exception as e:
                    assets[jobs[j]] = {"downloadError": str(e)}
                if n % 100 == 0:
                    print("Images", n, "/", len(jobs), flush=True)
        for m in mappings.values():
            m.update(assets[m["url"]])
    (ROOT / "data/catalog/image-mappings.json").write_text(
        json.dumps({"mappings": mappings, "gaps": gaps}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(
        "Matched",
        len(mappings),
        "/",
        len(cards),
        "CN",
        sum(m["locale"] == "zh-Hans" for m in mappings.values()),
        flush=True,
    )


if __name__ == "__main__":
    main()
