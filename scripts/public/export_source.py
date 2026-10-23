"""Build a checked source-only archive atomically; never publish partial output."""

import argparse
import hashlib
import json
from pathlib import Path
import os
import sys
import tempfile
import zipfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.public.policy import ROOT, source_files, safe_file, inspect


def export_source(output, root=ROOT):
    root, output = Path(root).resolve(), Path(output)
    checksum = output.with_name(output.name + ".sha256")
    if output.exists() or checksum.exists():
        raise ValueError("Output already exists")
    # Scan before writing; report only categories and relative paths, never values.
    sources = {}
    for name in source_files(root):
        content = safe_file(root, name).read_bytes()
        findings = inspect(name, content)
        if findings:
            raise ValueError(
                "Publication blocked: " + name + " [" + ",".join(findings) + "]"
            )
        sources[name] = content
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".public-source-", dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    hashes = {}
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in sources.items():
                if safe_file(root, name).read_bytes() != content:
                    raise ValueError("Source changed during export: " + name)
                info = zipfile.ZipInfo("PTCG-Lab/" + name)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (
                    0o100755 if name == "start.sh" else 0o100644
                ) << 16
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
        # Exclusive destination creation also protects against concurrent exports.
        os.link(temp, output)  # atomic, exclusive publication on the same filesystem
        sha = hashlib.file_digest(output.open("rb"), "sha256").hexdigest()
        with checksum.open("x") as target:
            target.write(sha + "  " + output.name + "\n")
        return {
            "archive": str(output),
            "files": len(hashes),
            "bytes": output.stat().st_size,
            "sha256": sha,
        }
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "exports/PTCG-Lab-public-source.zip"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(export_source(args.output)))
    except ValueError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
