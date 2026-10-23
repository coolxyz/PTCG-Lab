"""Scan tracked AND untracked source plus source archives; never print secret values."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.public.policy import (
    ROOT,
    inspect,
    allowed_source,
    safe_name,
    safe_file,
)


def git(*args, root=ROOT):
    return subprocess.check_output(["git", *args], cwd=root)


def audit_worktree(root=ROOT):
    tracked = set(filter(None, git("ls-files", "-z", root=root).decode().split("\0")))
    untracked = set(
        filter(
            None,
            git("ls-files", "--others", "--exclude-standard", "-z", root=root)
            .decode()
            .split("\0"),
        )
    )
    result = {"currentTrackedFindings": [], "currentUntrackedFindings": []}
    for key, names in [
        ("currentTrackedFindings", tracked),
        ("currentUntrackedFindings", untracked),
    ]:
        for name in sorted(names):
            p = Path(root) / name
            if not p.exists() and not p.is_symlink():
                continue  # deleted files are not published
            try:
                findings = inspect(name, safe_file(root, name).read_bytes())
            except ValueError:
                findings = ["symlink_or_unsafe_path"]
            if findings:
                result[key].append({"path": name, "categories": findings})
    return result


def audit_archive(path):
    findings = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            return [{"categories": ["duplicate_archive_entry"]}]
        if sum(i.file_size for i in infos) > 100_000_000:
            return [{"categories": ["archive_too_large"]}]
        try:
            manifest_bytes = archive.read("PTCG-Lab/PUBLIC-SOURCE.json")
            manifest_issues = inspect("README.md", manifest_bytes)
            if manifest_issues:
                return [{"path": "PUBLIC-SOURCE.json", "categories": manifest_issues}]
            manifest = json.loads(manifest_bytes)
            expected = manifest["files"]
            if (
                manifest.get("schema") != 1
                or manifest.get("containsGitHistory") is not False
                or manifest.get("containsRuntimeData") is not False
            ):
                raise ValueError()
            if not isinstance(expected, dict):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            return [{"categories": ["invalid_manifest"]}]
        actual = set()
        for info in infos:
            name = info.filename.removeprefix("PTCG-Lab/")
            if (
                not info.filename.startswith("PTCG-Lab/")
                or not safe_name(name)
                or (info.external_attr >> 16) & 0o170000 == 0o120000
            ):
                findings.append({"categories": ["unsafe_archive_entry"]})
                continue
            if name == "PUBLIC-SOURCE.json":
                continue
            actual.add(name)
            content = archive.read(info)
            issues = inspect(name, content)
            if not allowed_source(name):
                issues.append("not_allowlisted")
            if hashlib.sha256(content).hexdigest() != expected.get(name):
                issues.append("checksum_mismatch")
            if issues:
                findings.append({"path": name, "categories": issues})
        if actual != set(expected):
            findings.append({"categories": ["manifest_file_set_mismatch"]})
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--artifact", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero for findings in the requested scope",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "var/publication-audit.json"
    )
    args = parser.parse_args()
    report = audit_worktree()
    history, seen = [], set()
    if args.history:
        for commit in git("rev-list", "--all").decode().splitlines():
            for entry in git("ls-tree", "-r", "-z", commit).split(b"\0"):
                if not entry:
                    continue
                meta, name = entry.split(b"\t", 1)
                _, kind, oid = meta.split()
                # A blob under two different paths can have different privacy policy.
                key = (name, oid)
                if kind != b"blob" or key in seen:
                    continue
                seen.add(key)
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
    identifiable = sum(
        bool(e) and not e.endswith("@users.noreply.github.com") for e in emails
    )
    artifact = audit_archive(args.artifact) if args.artifact else []
    report.update(
        historyFindings=history,
        artifactFindings=artifact,
        nonNoreplyIdentityCount=identifiable,
        directPushRecommended=not (
            any(report.values()) or history or identifiable or artifact
        ),
        scope="Pattern and path scan, not proof of absence; ignored local state is excluded; values and identities redacted.",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    counts = {
        key: len(report[key])
        for key in (
            "currentTrackedFindings",
            "currentUntrackedFindings",
            "historyFindings",
            "artifactFindings",
        )
    }
    print(json.dumps(counts))
    if args.check and (any(counts.values()) or (args.history and identifiable)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
