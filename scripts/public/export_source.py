"""Export source without Git history, local state, datasets or third-party fixtures."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FOLDERS = (
    "apps/api",
    "apps/web/src",
    "apps/web/e2e",
    "packages",
    "scripts",
    "tests",
    "docs",
    "licenses",
    "rulesets",
)
FILES = (
    "README.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    ".gitignore",
    "start.sh",
    "start.ps1",
    "RELEASE.json",
)
EXCLUDED = {
    "__pycache__",
    "node_modules",
    ".venv",
    "test-results",
    "playwright-report",
    "fixtures",
}
ALLOWED = {
    ".py",
    ".ts",
    ".tsx",
    ".css",
    ".md",
    ".sh",
    ".ps1",
    ".txt",
    ".in",
    ".lock",
    ".json",
    ".html",
    ".mjs",
    ".cjs",
    ".svg",
}


def source_files(root=ROOT):
    names = set(FILES)
    for folder in FOLDERS:
        for p in (root / folder).rglob("*"):
            rel = p.relative_to(root)
            if (
                p.is_file()
                and not p.is_symlink()
                and not EXCLUDED.intersection(rel.parts)
                and p.suffix in ALLOWED
            ):
                # JSON under tests is captured third-party input, not original test code.
                if rel.parts[0] == "tests" and p.suffix == ".json":
                    continue
                names.add(rel.as_posix())
    for p in (root / "apps/web").iterdir():
        if p.is_file() and p.suffix in ALLOWED and not p.name.endswith(".tsbuildinfo"):
            names.add(p.relative_to(root).as_posix())
    return sorted(names)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "exports/PTCG-Lab-public-source.zip"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in source_files():
            p = ROOT / name
            if p.is_symlink():
                raise ValueError("Symlink not permitted: " + name)
            content = p.read_bytes()
            if re.search(
                rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
                content,
            ):
                raise ValueError("Possible credential in " + name)
            info = zipfile.ZipInfo("PTCG-Lab/" + name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name == "start.sh" else 0o100644) << 16
            archive.writestr(info, content)
            hashes[name] = hashlib.sha256(content).hexdigest()
        archive.writestr(
            "PTCG-Lab/PUBLIC-SOURCE.json",
            json.dumps(
                {
                    "schema": 1,
                    "containsGitHistory": False,
                    "containsRuntimeData": False,
                    "files": hashes,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    sha = hashlib.file_digest(args.output.open("rb"), "sha256").hexdigest()
    args.output.with_name(args.output.name + ".sha256").write_text(
        sha + "  " + args.output.name + "\n"
    )
    print(
        json.dumps(
            {
                "archive": str(args.output),
                "files": len(hashes),
                "bytes": args.output.stat().st_size,
                "sha256": sha,
            }
        )
    )


if __name__ == "__main__":
    main()
