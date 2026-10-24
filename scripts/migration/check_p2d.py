"""Bind a completed private P2-D acceptance to its public source inputs.

Public CI verifies this attestation and synthetic contracts. It cannot rerun real
card-asset tests without separately authorized assets; the document states that.
"""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    "prize",
    "face-up-prize",
    "replacement",
    "deck-search",
    "non-turn-actor",
    "hidden-selection",
    "terminal",
}


def source_hashes(root=ROOT):
    root = Path(root)
    files = []
    for folder in (
        "packages/battle-core",
        "packages/engine-provider",
        "packages/battle-protocol",
        "tests/core",
        "tests/protocol",
    ):
        for path in (root / folder).rglob("*"):
            if (
                path.is_file()
                and path.suffix in {".py", ".json", ".ts", ".toml", ".lock"}
                and not any(
                    part in {"__pycache__", "build", "dist", "node_modules"}
                    or part.endswith(".egg-info")
                    for part in path.parts
                )
            ):
                files.append(path)
    files += [
        root / "scripts/migration" / name
        for name in ("p2d_worker.py", "p2d_acceptance.py", "core_regressions.py")
    ]
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(files)
    }


def check(root=ROOT):
    root = Path(root)
    status = json.loads((root / "releases/migration-status.json").read_text())
    if status.get("subphases", {}).get("P2-D") != "completed":
        return []
    path = root / "releases/p2d-acceptance.json"
    if not path.is_file():
        return ["P2-D marked complete without acceptance evidence"]
    report = json.loads(path.read_text())
    errors = []
    if (
        report.get("status") != "passed"
        or report.get("naturalMatches", 0) < 4
        or report.get("directedScenes", 0) < 8
        or report.get("restoreCheckpoints", 0) < 4
        or report.get("crossProcessResumeCount", 0) < 5
        or report.get("commands", 0) != report.get("interleavedCommands")
        or not report.get("commands")
        or not REQUIRED <= set(report.get("coverage", []))
        or report.get("productionRoutingChanged") is not False
    ):
        errors.append("P2-D acceptance evidence incomplete")
    if report.get("sourceHashes") != source_hashes(root):
        errors.append(
            "P2-D accepted source drift; rerun affected acceptance before claiming completion"
        )
    return errors
