"""Capture a source baseline, optionally stage a private isolated legacy app copy.

No user database, credentials, .catalog state, model or running process is touched.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import source_files, safe_file  # noqa: E402


def capture(output, root=ROOT, stage=False, assets=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Output exists; use a new baseline directory")
    if output.is_relative_to(root) and output.relative_to(root).parts[0] != "var":
        raise ValueError(
            "Store local baseline reports under var/ or outside the repository"
        )
    if assets and not stage:
        raise ValueError("Asset copying requires --stage-app")
    selected = set(source_files(root))
    asset_names = set()
    required = [
        "data/catalog/catalog.json",
        "runtime/engine/ptcg/core/envs.py",
        "artifacts/engine/overlay-hashes.json",
    ]
    availability = {n: (root / n).is_file() for n in required}
    if assets:
        if not all(availability.values()):
            raise ValueError(
                "Missing baseline runtime assets; do not bypass integrity checks"
            )
        for folder in ["data", "runtime/engine"]:
            for p in (root / folder).rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                    asset_names.add(p.relative_to(root).as_posix())
        asset_names.add("artifacts/engine/overlay-hashes.json")
    # Only this explicit asset set is copied; var/.catalog/model directories are never selected.
    selected |= asset_names
    hashes = {
        n: hashlib.sha256(safe_file(root, n).read_bytes()).hexdigest()
        for n in sorted(selected)
    }
    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        status = (
            subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=root)
            .decode()
            .split("\0")
        )
    except subprocess.CalledProcessError:
        commit = None
        status = ["not-a-git-checkout"]
    output.mkdir(parents=True)
    if stage:
        for name in hashes:
            target = output / "app" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_file(root, name), target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashes[name]:
                raise ValueError("Source changed during baseline staging: " + name)
    for name, expected in hashes.items():
        if hashlib.sha256(safe_file(root, name).read_bytes()).hexdigest() != expected:
            raise ValueError("Source changed during baseline capture: " + name)
    report = {
        "schema": 1,
        "sourceCommit": commit,
        "worktreeChanges": list(filter(None, status)),
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "runtimeAssetAvailability": availability,
        "assetFiles": len(asset_names),
        "files": hashes,
        "sourceDigest": hashlib.sha256(
            json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "privateReport": True,
        "userDatabasesCopied": False,
        "appStaged": stage,
    }
    (output / "baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage-app", action="store_true")
    parser.add_argument(
        "--with-runtime-assets",
        action="store_true",
        help="Copy only existing trusted local data/engine assets into the private test copy",
    )
    args = parser.parse_args()
    report = capture(args.output, stage=args.stage_app, assets=args.with_runtime_assets)
    print(
        json.dumps(
            {
                "sourceFiles": len(report["files"]),
                "assetFiles": report["assetFiles"],
                "userDatabasesCopied": False,
            }
        )
    )
