"""Copy legally obtained private runtime assets into a new source checkout.

Use only your own trusted, compatible project directory. No download or code execution.
"""

import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(ROOT))
from scripts.native.layout import DATA, install_artwork_index


def asset_names(source):
    names = ["data/" + name for name in DATA]
    names += [p.relative_to(source).as_posix() for p in sorted((source / "data/sync").glob("*.json"))]
    names += ["runtime/engine", "artifacts/engine/overlay-hashes.json"]
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    src = args.source.resolve()
    names = asset_names(src)
    # Fail before copying any bytes when the source is not the matching catalog.
    install_artwork_index(ROOT, src, ROOT / "var/asset-import-check")
    for name in names:
        path = src / name
        if not path.exists() or (ROOT / name).exists():
            raise SystemExit("Missing source or destination already exists: " + name)
        if path.is_symlink() or (
            path.is_dir() and any(p.is_symlink() for p in path.rglob("*"))
        ):
            raise SystemExit("Symlink not allowed: " + name)
    for name in names:
        path = src / name
        dst = ROOT / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, dst)
        else:
            shutil.copy2(path, dst)
    print(
        "Local assets imported. No user database, credentials or Git history copied. Run setup and --check."
    )


if __name__ == "__main__":
    main()
