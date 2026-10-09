"""Copy legally obtained private runtime assets into a new source checkout.

Use only your own trusted, compatible project directory. No download or code execution.
"""

import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("data", "runtime/engine", "artifacts/engine/overlay-hashes.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    src = args.source.resolve()
    for name in NAMES:
        path = src / name
        if not path.exists() or (ROOT / name).exists():
            raise SystemExit("Missing source or destination already exists: " + name)
        if path.is_symlink() or (
            path.is_dir() and any(p.is_symlink() for p in path.rglob("*"))
        ):
            raise SystemExit("Symlink not allowed: " + name)
    for name in NAMES:
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
