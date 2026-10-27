"""Assemble a private macOS P8-A payload; never copy existing user state.

The explicitly supplied asset source is read-only. Only approved DATA, runtime
and frozen overlay hashes are read there; source code comes from this worktree.
Build dependencies/network are needed by the developer, never by installed users.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import safe_file, source_files  # noqa: E402
from scripts.product.kit import DATA, PROJECTS  # noqa: E402

LEGACY = (
    "packages/collection/",
    "packages/cardpool/",
    "packages/sync/",
    "packages/rules/",
    "packages/engine_adapter/",
    "packages/simulation/",
    "packages/battle/",
)


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def unpack_runtime(archive, target, lock):
    if sha256(Path(archive).read_bytes()).hexdigest() != lock["sha256"]:
        raise ValueError("Runtime archive digest mismatch")
    with tempfile.TemporaryDirectory(prefix="ptcg-native-runtime-") as temp:
        with tarfile.open(archive) as tar:
            # Python's data filter rejects escaping links, traversal and device files.
            tar.extractall(temp, filter="data")
        source = Path(temp) / "python"
        for p in source.rglob("*"):
            if p.is_symlink() and not p.resolve().is_relative_to(source.resolve()):
                raise ValueError("Runtime link escapes archive")
        # Resolve internal runtime links to regular files for installation auditing.
        shutil.copytree(source, target, symlinks=False)


def assemble(archive, assets, output, *, root=ROOT):
    output, assets, root = (
        Path(output).absolute(),
        Path(assets).resolve(),
        Path(root).resolve(),
    )
    if output.exists():
        raise ValueError("Choose a new payload directory")
    lock = json.loads((root / "scripts/native/runtime-lock.json").read_text())
    output.mkdir(parents=True)
    unpack_runtime(archive, output / "python", lock)
    app = output / "app"
    for name in source_files(root):
        if name in {
            "packages/__init__.py",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "scripts/engine/build_engine.py",
        } or name.startswith(("apps/api/", "rulesets/", *LEGACY)):
            copy_file(safe_file(root, name), app / name)
    for name in DATA:
        copy_file(safe_file(assets, "data/" + name), app / "data" / name)
    for p in (assets / "data/sync").glob("*.json"):
        name = p.relative_to(assets).as_posix()
        copy_file(safe_file(assets, name), app / name)
    for p in (assets / "runtime/engine").rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            name = p.relative_to(assets).as_posix()
            copy_file(safe_file(assets, name), app / name)
    copy_file(
        safe_file(assets, "artifacts/engine/overlay-hashes.json"),
        app / "artifacts/engine/overlay-hashes.json",
    )
    for folder, entry in (
        ("apps/web/dist", "index.html"),
        ("apps/desktop-web/dist", "online.html"),
    ):
        safe_file(root, folder + "/" + entry)
        for p in (root / folder).rglob("*"):
            if p.is_file():
                name = p.relative_to(root).as_posix()
                copy_file(safe_file(root, name), app / name)
    python = output / "python/bin/python3"
    command = [
        str(python),
        "-I",
        "-B",
        "-m",
        "pip",
        "install",
        "--no-compile",
        "--disable-pip-version-check",
    ]
    subprocess.run(
        command + ["--no-deps", "-r", str(root / "scripts/native/python-lock.txt")],
        check=True,
    )
    # All internal distributions are supplied together, avoiding registry lookups
    # for the repository's unpublished development versions.
    subprocess.run(
        command
        + ["--no-deps", "--no-build-isolation", *[str(root / p) for p in PROJECTS]],
        check=True,
    )
    (output / "bootstrap.py").write_text(
        "import pathlib, runpy, sys\n"
        'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "app"))\n'
        'runpy.run_module("apps.api.native_product", run_name="__main__")\n'
    )
    # Remove build/test tools and caches from the installed payload. Dependency
    # trimming beyond these explicit tools belongs to P8-C's signed installer.
    for p in output.rglob("__pycache__"):
        shutil.rmtree(p)
    for p in (output / "python/lib").glob(
        "python*/site-packages/*.dist-info/direct_url.json"
    ):
        p.unlink()  # pip's local build paths must never enter an installed bundle.
    manifest = {
        "schema": 1,
        "platform": lock["platform"],
        "python": "python/bin/python3",
        "files": {
            p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    return {"files": len(manifest["files"]), "platform": lock["platform"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args.runtime, args.assets, args.output)))
