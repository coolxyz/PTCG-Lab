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
from scripts.product.git_artwork import GitCache, plan, encoded  # noqa: E402
from packages.sync.http_source import parse  # noqa: E402
from packages.sync.normalize import COSMETIC, normalize  # noqa: E402


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


def build_snapshot(catalog_path, cache_path, commit, output, repository):
    """All explicit upstream face IDs, from verified Git metadata; no image download."""
    cache = GitCache(cache_path, repository)
    cache.initialize()
    if cache.git("rev-parse", commit + "^{commit}", no_lazy=True).decode().strip() != commit:
        raise ValueError("Pinned Git commit differs")
    tree = cache.tree(commit)
    cache.prefetch([tree["ptcg_chs_infos.json"]])
    upstream = parse(cache.blob(tree["ptcg_chs_infos.json"], 96 * 1024**2))
    # Match Sync's canonical face selection exactly: bundled products can repeat
    # a card ID under a different image path. The first normalized card owns it.
    normalized = normalize(upstream, tree)
    images = {}
    for cid, row in normalized["cards"].items():
        if not row["imagePath"]:
            continue
        if not cid.isdecimal() or str(row["raw"]["id"]) != cid or not row["imageBlob"]:
            raise ValueError("Upstream face identity differs")
        images[cid] = {"path": row["imagePath"], "blob": row["imageBlob"]}
    value = {"schema": 1, "repository": repository, "commit": commit,
             "catalogFileSha256": sha256(Path(catalog_path).read_bytes()).hexdigest(), "images": images}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open("xb") as stream:
        stream.write(encoded(value) + b"\n")
    return {"faces": len(images), "sha256": sha256(Path(output).read_bytes()).hexdigest(), "imageDownloads": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("catalog", "cache", "commit", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--repository", default="https://github.com/duanxr/PTCG-CHS-Datasets"
    )
    parser.add_argument("--snapshot", action="store_true", help="Generate the full face-ID metadata companion instead of the primary printing index")
    args = parser.parse_args()
    print(
        json.dumps(
            (build_snapshot if args.snapshot else build)(args.catalog, args.cache, args.commit, args.output, args.repository)
        )
    )
