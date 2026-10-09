"""Resume missing card article acquisition without overwriting the original snapshot."""

import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.catalog.card_pages import batch  # noqa: E402


def main():
    inventory = json.loads(
        (ROOT / "artifacts/cardpool/target-inventory.json").read_text(encoding="utf-8")
    )
    output = ROOT / ".catalog/cardpool/card-pages.json"
    result = (
        json.loads(output.read_text(encoding="utf-8"))["pages"]
        if output.exists()
        else {}
    )
    titles = sorted(
        {
            c["cardPage"]
            for c in inventory["cards"]
            if c.get("cardPage")
            and "CARD_ARTICLE_MISSING" in c["gaps"]
            and c["cardPage"] not in result
        }
    )
    groups = [titles[i : i + 20] for i in range(0, len(titles), 20)]
    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(batch, group): group for group in groups}
        for i, job in enumerate(as_completed(jobs), 1):
            try:
                result.update(job.result())
            except Exception as exc:
                errors.append({"titles": jobs[job], "error": str(exc)})
            output.write_text(
                json.dumps(
                    {"pages": result, "errors": errors}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            print(
                f"{i}/{len(groups)} batches, {len(result)} articles, {len(errors)} errors",
                flush=True,
            )


if __name__ == "__main__":
    main()
