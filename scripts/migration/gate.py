# ruff: noqa: E402
"""P1 public-source gate. No application startup or asset import."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.check_boundaries import check
from scripts.public.audit import audit_worktree, audit_archive
from scripts.public.export_source import export_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-output",
        type=Path,
        help="Also build and inspect all scaffolds in a new directory",
    )
    args = parser.parse_args()
    errors = check()
    findings = audit_worktree()
    if errors or any(findings.values()):
        print(
            json.dumps(
                {"boundaryErrors": errors, "publicationFindings": findings},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)
    subprocess.run(
        [sys.executable, "scripts/protocol/generate.py", "--check"],
        cwd=ROOT,
        check=True,
    )
    with tempfile.TemporaryDirectory(prefix="ptcg-public-gate-") as temp:
        output = Path(temp) / "source.zip"
        export_source(output)
        problems = audit_archive(output)
        if problems:
            print(json.dumps(problems))
            raise SystemExit(1)
    if args.build_output:
        from scripts.migration.build_scaffolds import build

        build(args.build_output)
    print(
        json.dumps(
            {
                "boundaries": "passed",
                "trackedAndUntrackedScan": "passed",
                "generatedProtocol": "passed",
                "sourceArtifact": "passed",
                "scaffoldBuild": "passed" if args.build_output else "not-requested",
            }
        )
    )


if __name__ == "__main__":
    main()
