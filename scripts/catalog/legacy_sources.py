"""Record availability of old official PDF links cited by wiki file pages."""

from pathlib import Path
import sys, json, re
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.sources import ROOT, retrieve


def main():
    pages = json.loads((ROOT / ".catalog/image-pages.json").read_text())["pages"]
    urls = sorted(
        {
            u
            for p in pages.values()
            for u in re.findall(r"https?://[^\s\]|}<>]+", p.get("text") or "")
            if u.startswith("https://tw.portal-pokemon.com/card/pdf/")
        }
    )
    result = {}

    def check(url):
        try:
            data, path = retrieve(url, "pdf")
            return {
                "bytes": len(data),
                "pdf": data.startswith(b"%PDF"),
                "cachePath": str(path.relative_to(ROOT)),
            }
        except Exception as e:
            return {"error": str(e)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(check, u): u for u in urls}
        for j in as_completed(jobs):
            result[jobs[j]] = j.result()
    (ROOT / "data/catalog/legacy-pdf-sources.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        "PDF sources",
        len(urls),
        "available",
        sum(bool(x.get("pdf")) for x in result.values()),
    )


if __name__ == "__main__":
    main()
