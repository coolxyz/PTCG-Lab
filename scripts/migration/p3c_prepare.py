"""Build both GUI variants and stage a fresh full-product acceptance copy."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.baseline import capture


def prepare(output):
    output = Path(output).resolve()
    capture(output, stage=True, assets=True)
    subprocess.run(
        ["npm", "run", "p3:product:build", "--", "--outDir", str(output / "frontend")],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["npm", "run", "build", "--", "--outDir", str(output / "frontend-legacy")],
        cwd=ROOT / "apps/web",
        check=True,
    )
    shutil.copytree(output / "frontend", output / "app/apps/web/dist")
    print(
        "Both GUI builds retained; staged product only. No user database or service reused."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output)
