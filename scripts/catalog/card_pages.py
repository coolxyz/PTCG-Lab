"""Batch MediaWiki revisions; 20 titles/request, two workers, cached retry."""

from pathlib import Path
import sys, json, urllib.parse, hashlib, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.catalog.sources import ROOT, retrieve


def batch(titles):
    url = "https://wiki.52poke.com/api.php?" + urllib.parse.urlencode(
        {
            "action": "query",
            "titles": "|".join(titles),
            "prop": "revisions",
            "rvprop": "ids|content",
            "rvslots": "main",
            "redirects": "1",
            "converttitles": "1",
            "format": "json",
            "formatversion": "2",
        }
    )
    data, path = retrieve(url, "json")
    raw = json.loads(data)
    if "error" in raw:
        raise ValueError(raw["error"])
    q = raw["query"]
    aliases = {
        x["from"]: x["to"]
        for key in ("normalized", "converted", "redirects")
        for x in q.get(key, [])
    }
    pages = {p["title"]: p for p in q["pages"]}
    result = {}
    for name in titles:
        target = name
        for _ in range(10):
            if target not in aliases:
                break
            target = aliases[target]
        p = pages.get(target, {})
        revision = next(iter(p.get("revisions", [])), {})
        result[name] = {
            "title": target,
            "pageid": p.get("pageid"),
            "revision": revision.get("revid"),
            "text": revision.get("slots", {}).get("main", {}).get("content"),
            "sourceUrl": "https://wiki.52poke.com/wiki/" + urllib.parse.quote(target),
            "responseSha256": hashlib.sha256(data).hexdigest(),
            "cachePath": str(path.relative_to(ROOT)),
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", action="store_true")
    args = parser.parse_args()
    if args.files:
        inputs = json.loads((ROOT / "data/catalog/enriched.json").read_text())
        titles = sorted(
            {
                "File:" + i["file"]
                for c in inputs["cards"]
                for i in c.get("imageCandidates", [])
                if i.get("file")
            }
        )
        out = ROOT / ".catalog/image-pages.json"
    else:
        inputs = json.loads((ROOT / "data/catalog/card-tables.json").read_text())
        titles = sorted({c["cardPage"] for c in inputs["cards"] if c.get("cardPage")})
        out = ROOT / ".catalog/card-pages.json"
    result = json.loads(out.read_text()).get("pages", {}) if out.exists() else {}
    errors = []
    titles = [t for t in titles if t not in result]
    groups = [titles[i : i + 20] for i in range(0, len(titles), 20)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(batch, g): g for g in groups}
        for i, job in enumerate(as_completed(jobs), 1):
            try:
                result.update(job.result())
            except Exception as e:
                errors.append({"titles": jobs[job], "error": str(e)})
            if i % 10 == 0 or i == len(groups):
                out.write_text(
                    json.dumps({"pages": result, "errors": errors}, ensure_ascii=False)
                )
                print(
                    i,
                    "/",
                    len(groups),
                    "batches;",
                    len(result),
                    "pages;",
                    len(errors),
                    "errors",
                    flush=True,
                )
    print("Complete", out, flush=True)
