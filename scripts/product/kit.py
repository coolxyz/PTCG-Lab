"""Build an opt-in full-product test kit, with explicitly approved runtime/artwork.

No user database, native identity, virtualenv, Git history or research outputs are
copied. This private acceptance kit is separate from the public source archive.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.public.policy import source_files, safe_file  # noqa: E402
from scripts.product.resources import export_pack  # noqa: E402

from scripts.native.layout import DATA, PROJECTS  # noqa: E402


def assemble(output, wheels, image_home, origin, ca, *, root=ROOT):
    output, wheels, root = (
        Path(output).absolute(),
        Path(wheels).resolve(),
        Path(root).resolve(),
    )
    if output.exists():
        raise ValueError("Choose a new kit directory")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
        or origin != origin.lower()
    ):
        raise ValueError("Use a canonical HTTPS API origin")
    certificate = Path(ca).read_bytes()
    if b"PRIVATE KEY" in certificate:
        raise ValueError("Supply only a public CA certificate")
    ssl.create_default_context(cadata=certificate.decode("ascii"))
    files = {"app/" + n: safe_file(root, n) for n in source_files(root)}
    for name in DATA:
        files["app/data/" + name] = safe_file(root, "data/" + name)
    for path in (root / "data/sync").glob("*.json"):
        files["app/" + path.relative_to(root).as_posix()] = safe_file(
            root, path.relative_to(root).as_posix()
        )
    for path in (root / "runtime/engine").rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files["app/" + path.relative_to(root).as_posix()] = safe_file(
            root, path.relative_to(root).as_posix()
        )
    for name in ("artifacts/engine/overlay-hashes.json",):
        files["app/" + name] = safe_file(root, name)
    for folder, entry in (
        ("apps/web/dist", "index.html"),
        ("apps/desktop-web/dist", "online.html"),
    ):
        safe_file(root, folder + "/" + entry)
        for path in (root / folder).rglob("*"):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                files["app/" + name] = safe_file(root, name)
    for project in PROJECTS:
        meta = tomllib.loads((root / project / "pyproject.toml").read_text())["project"]
        name = (
            meta["name"].replace("-", "_") + "-" + meta["version"] + "-py3-none-any.whl"
        )
        files["wheels/" + name] = safe_file(wheels, name)
    files["manage.py"] = root / "scripts/product/manage_kit.py"
    files["README.md"] = root / "docs/启动使用与跨系统迁移.md"
    output.mkdir(parents=True)
    for name, source in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (output / "ca.pem").write_bytes(certificate)
    (output / "connection.json").write_text(json.dumps({"origin": origin}) + "\n")
    artwork = export_pack(image_home, output / "artwork")
    records = {
        p.relative_to(output).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
    manifest = {
        "schema": 1,
        "stage": "P5.5-D",
        "sourceCommit": head,
        "worktreeChangesIncluded": dirty,
        "physicalDeviceAcceptance": "pending",
        "userDatabasesCopied": False,
        "nativeIdentitiesCopied": False,
        "artwork": artwork,
        "files": records,
    }
    (output / "KIT-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "files": len(records),
        "artwork": artwork,
        "physicalDeviceAcceptance": "pending",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "wheels", "image-home", "ca"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--with-approved-resources", action="store_true", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            assemble(args.output, args.wheels, args.image_home, args.origin, args.ca)
        )
    )
