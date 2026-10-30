"""Build a lightweight, exact printing-to-artwork index from already pinned Git objects.

Does not download artwork, alter a catalog, touch a user profile, or install resources.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.product.git_card_images import GitCache, plan, encoded  # noqa: E402
from packages.sync.http_source import parse  # noqa: E402
from packages.sync.normalize import COSMETIC  # noqa: E402


def build(catalog_path, cache_path, commit, output, repository):
    catalog_path = Path(catalog_path)
    raw = catalog_path.read_bytes()
    catalog = parse(raw)
    cache = GitCache(cache_path, repository)
    cache.initialize()
    actual = cache.git("rev-parse", commit + "^{commit}", no_lazy=True).decode().strip()
    if actual != commit:
        raise ValueError("Pinned Git commit differs")
    tree = cache.tree(commit)
    upstream = parse(cache.blob(tree["ptcg_chs_infos.json"], 96 * 1024**2))
    selection = plan(catalog, upstream, tree, cosmetic=COSMETIC)
    value = {
        "schema": 1,
        "repository": repository,
        "commit": commit,
        "catalogFileSha256": sha256(raw).hexdigest(),
        "cards": [
            {k: row[k] for k in ("printingId", "engineId", "path", "blob")}
            for row in selection["selected"]
        ],
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open("xb") as stream:
        stream.write(encoded(value) + b"\n")
    return {
        "cards": len(value["cards"]),
        "skipped": len(selection["skipped"]),
        "sha256": sha256(Path(output).read_bytes()).hexdigest(),
        "imageDownloads": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "cache", "commit", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--repository", default="https://github.com/duanxr/PTCG-CHS-Datasets"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.catalog, args.cache, args.commit, args.output, args.repository)
        )
    )
