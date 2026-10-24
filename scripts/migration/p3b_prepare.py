"""Prepare an isolated practice acceptance copy; never start an existing service."""

import argparse
import os
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
        [
            "npm",
            "run",
            "build",
            "--workspace",
            "@ptcg/desktop-web",
            "--",
            "--outDir",
            str(output / "frontend"),
        ],
        cwd=ROOT,
        env={**os.environ, "PTCG_PRACTICE_MODE": "1"},
        check=True,
    )
    shutil.copytree(output / "frontend", output / "app/apps/web/dist")
    print("Isolated practice copy prepared; no database or service was reused.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output)
