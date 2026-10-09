"""Resolve CN 151 assets from the resource pattern in the official page's JS."""

from pathlib import Path
import sys, json, re, hashlib, struct
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.artwork import fetch
from scripts.catalog.sources import ROOT

PAGE = "https://special.pokemon.cn/151/"


def main():
    html, _ = fetch(PAGE)
    text = html.decode()
    # Webpack's public runtime explicitly identifies the desktop page chunk.
    chunk = re.search(r'5:"([0-9a-f]+)"\}\[i\]\+"\.chunk.js"', text)
    if not chunk:
        raise ValueError("官网资源结构已变化，需重新核对")
    script_url = PAGE + "static/js/5." + chunk[1] + ".chunk.js"
    js, _ = fetch(script_url, "js")
    code = js.decode()
    if (
        "Array.from({length:151})" not in code
        or "https://special.pokemon.cn/151/img/big/151_" not in code
        or "s.on_act(s)" in code
    ):
        raise ValueError("官网编号构造结构未确认")
    numbers = {str(i).zfill(3) for i in range(1, 152)} | set(
        re.findall(r'show_big\("(\d+)"\)', code)
    )
    cards = {
        c["collectorNumber"]: c
        for c in json.loads((ROOT / "data/catalog/enriched.json").read_text())["cards"]
        if c["productCode"] == "151C"
    }
    out = {}
    errors = {}

    def one(n):
        url = PAGE + "img/big/151_" + n + ".png"
        b, p = fetch(url, "img")
        if b[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("响应不是PNG")
        w, h = struct.unpack(">II", b[16:24])
        if w < 300 or h < 400:
            raise ValueError("卡图尺寸异常")
        sha = hashlib.sha256(b).hexdigest()
        dest = ROOT / ".catalog/images" / (sha + ".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b)
        return {
            "url": url,
            "source": PAGE,
            "locale": "zh-Hans",
            "fileEvidence": cards[n]["source"]["url"],
            "resourceEvidence": script_url,
            "resourceSha256": hashlib.sha256(js).hexdigest(),
            "matchMethod": "official-151-page-numbered-resource",
            "equivalenceReviewed": True,
            "sha256": sha,
            "bytes": len(b),
            "width": w,
            "height": h,
            "localUrl": "/card-images/" + dest.name,
            "localPath": str(dest.relative_to(ROOT)),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(one, n): n for n in numbers if n in cards}
        for i, j in enumerate(as_completed(jobs), 1):
            n = jobs[j]
            try:
                out["CN:151C:" + n] = j.result()
            except Exception as e:
                errors[n] = str(e)
            if i % 30 == 0:
                print(i, "/", len(jobs), flush=True)
    (ROOT / "data/catalog/cn151-images.json").write_text(
        json.dumps({"mappings": out, "errors": errors}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print("CN151 images", len(out), "errors", errors, flush=True)


if __name__ == "__main__":
    main()
