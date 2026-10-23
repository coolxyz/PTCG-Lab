"""Keep legacy execution bytes frozen while new rules evolve in ptcg_core."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def check(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / "releases/legacy-core-freeze.json").read_text())
    errors = []
    for name, expected in manifest["files"].items():
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            errors.append(name + ": frozen legacy runtime changed")
    for path in (root / "packages/rules").glob("*.py"):
        if path.relative_to(root).as_posix() not in manifest["files"]:
            errors.append(path.name + ": new implementation added to frozen rules")
    return errors
