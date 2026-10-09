"""Offline evidence for the published catalogue, alongside P1 browser checks."""

from pathlib import Path
import sys, subprocess, json, hashlib, time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/catalog"


def main():
    start = time.monotonic()
    cmd = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "pytest",
        "tests/catalog",
        "tests/collection",
        "tests/engine_adapter/test_cn_format.py",
        "-q",
        "--junitxml=artifacts/catalog/tests.xml",
    ]
    with (OUT / "tests.log").open("w") as f:
        result = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    paths = [
        p
        for folder in ("scripts/catalog", "tests/catalog", "data/catalog")
        for p in (ROOT / folder).glob("*")
        if p.is_file() and ".tmp." not in p.name
    ]
    report = {
        "execution": "complete" if result.returncode == 0 else "failed",
        "exitCode": result.returncode,
        "seconds": round(time.monotonic() - start, 2),
        "command": cmd,
        "catalogVersion": json.loads((ROOT / "data/catalog/catalog.json").read_text())[
            "version"
        ],
        "sourceHashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        },
        "coverage": "coverage.json",
        "testLog": "tests.log",
        "browserEvidence": "../collection/summary.json",
    }
    (OUT / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(report["execution"])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
