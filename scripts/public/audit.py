"""Read-only tracked-file/history scan. Reports categories, never matching secrets."""

import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PATTERNS = {
    "credential_pattern": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    "machine_path": rb"(?:/Users/[A-Za-z0-9_-]+/|/Volumes/[A-Za-z0-9_-]+/|[A-Z]:[\\/](?:Users|Work)[\\/])",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def inspect(name, content):
    found = [key for key, pattern in PATTERNS.items() if re.search(pattern, content)]
    if name.startswith(
        ("var/", ".catalog/", ".p1/", ".p0/", ".p01/", "exports/", "outputs/")
    ) or name in ("MIGRATION.json", "TRANSFER-MANIFEST.json"):
        found.append("local_or_generated_state")
    if (
        name.startswith("data/")
        or name.startswith("tests/fixtures/")
        or name == "tests/sync/real-recovery.json"
    ):
        found.append("third_party_data_review")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "var/publication-audit.json"
    )
    args = parser.parse_args()
    current = []
    for raw in git("ls-files", "-z").split(b"\0"):
        if not raw:
            continue
        name = raw.decode()
        p = ROOT / name
        if p.is_file():
            issues = inspect(name, p.read_bytes())
            if issues:
                current.append({"path": name, "categories": issues})
    history = []
    seen = set()
    if args.history:
        for commit in git("rev-list", "--all").decode().splitlines():
            for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
                if not entry:
                    continue
                meta, name = entry.split(b"\t", 1)
                mode, kind, oid = meta.split()
                if kind != b"blob" or oid in seen:
                    continue
                seen.add(oid)
                issues = inspect(name.decode(), git("cat-file", "blob", oid.decode()))
                if issues:
                    history.append(
                        {
                            "commit": commit[:12],
                            "path": name.decode(),
                            "categories": issues,
                        }
                    )
    emails = set(git("log", "--all", "--format=%ae%n%ce").decode().splitlines())
    identifiable = [
        e for e in emails if e and not e.endswith("@users.noreply.github.com")
    ]
    report = {
        "currentTrackedFindings": current,
        "historyFindings": history,
        "nonNoreplyIdentityCount": len(identifiable),
        "directPushRecommended": not (current or history or identifiable),
        "scope": "Pattern scan, not proof of absence. Names/emails are counted but redacted. Source-only export excludes datasets, generated state and all Git history.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "trackedFindings": len(current),
                "historyFindings": len(history),
                "nonNoreplyIdentityCount": len(identifiable),
                "report": str(args.output),
                "directPushRecommended": report["directPushRecommended"],
            }
        )
    )


if __name__ == "__main__":
    main()
