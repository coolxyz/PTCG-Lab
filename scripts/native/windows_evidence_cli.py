"""Collect real native B4 automation results, never synthetic/Mac acceptance."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.native.windows_environment import facts, eligible  # noqa: E402
from scripts.native.windows_evidence import collect  # noqa: E402

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    a = p.parse_args()
    current = facts()
    if not eligible(current) or current != json.loads(
        (a.run / "environment.json").read_text(encoding="utf-8")
    ):
        raise SystemExit("Native standard-user Windows evidence required")
    result = collect(a.run, root=ROOT)
    with (a.run / "REPORT.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
