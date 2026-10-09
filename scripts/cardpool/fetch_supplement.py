"""Acquire explicit supplementary wiki titles with resumable revision evidence."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.catalog.card_pages import batch  # noqa: E402


def fetch_group(titles):
    try:
        return batch(titles)
    except HTTPError as exc:
        if exc.code not in (502, 503, 504) or len(titles) == 1:
            raise
        middle = len(titles) // 2
        return {**fetch_group(titles[:middle]), **fetch_group(titles[middle:])}


def run(titles, output):
    result = (
        json.loads(output.read_text(encoding="utf-8"))["pages"]
        if output.exists()
        else {}
    )
    pending = sorted(set(titles) - result.keys())
    groups = [pending[i : i + 20] for i in range(0, len(pending), 20)]
    errors = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(fetch_group, group): group for group in groups}
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
            print(f"{i}/{len(groups)} batches; {len(errors)} errors", flush=True)
    return not errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("titles", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raise SystemExit(
        0
        if run(json.loads(args.titles.read_text(encoding="utf-8")), args.output)
        else 1
    )
