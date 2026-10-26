"""Assemble an opt-in desktop test kit from checked wheels and the online build.

No user configuration, rule inputs, images, credentials or server package is copied.
The three platform-independent wheels still require Python 3.14 and platform-specific
external dependencies installed separately on the target machine.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tomllib

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ("packages/battle-protocol", "packages/online-client", "apps/desktop-api")


def assemble(packages, web, output):
    packages, web, output = map(Path, (packages, web, output))
    files = {}
    for project in PROJECTS:
        meta = tomllib.loads((ROOT / project / "pyproject.toml").read_text())["project"]
        name = (
            meta["name"].replace("-", "_") + "-" + meta["version"] + "-py3-none-any.whl"
        )
        files["wheels/" + name] = packages / name
    files["web/online.html"] = web / "online.html"
    assets = list((web / "assets").glob("*"))
    if not assets:
        raise ValueError("Missing isolated web assets")
    for file in assets:
        if file.suffix not in (".js", ".css") or not file.is_file():
            raise ValueError("Unexpected online web asset")
        files["web/assets/" + file.name] = file
    files["requirements.txt"] = ROOT / "apps/desktop-api/requirements.lock"
    files["verify.py"] = ROOT / "scripts/migration/p5d_verify_bundle.py"
    files["README.md"] = ROOT / "docs/P5D双设备手动测试.md"
    files["acceptance-template.json"] = ROOT / "releases/p5d-manual-template.json"
    for file in files.values():
        if file.is_symlink() or not file.is_file():
            raise ValueError("Missing or linked test kit input")
    output.mkdir(parents=True, exist_ok=False)
    records = {}
    for relative, source in files.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records[relative] = sha256(target.read_bytes()).hexdigest()
    (output / "KIT-MANIFEST.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "stage": "P5-D",
                "physicalDeviceAcceptance": "pending",
                "files": records,
            },
            indent=2,
        )
        + "\n"
    )
    return {"status": "assembled", "files": len(records), "privateDataIncluded": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--web", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args.packages, args.web, args.output)))
