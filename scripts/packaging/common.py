"""Shared preview packaging primitives. Generated release/ is never public source."""

from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def run(args, *, cwd=ROOT, env=None, capture=False):
    return subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def new_output(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Output must not contain symlinks")
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    return path


def write(path, content, *, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def record(path, value):
    write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def file_hash(path):
    with Path(path).open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def provenance():
    from scripts.public.policy import source_files

    digest = sha256()
    for name in source_files(ROOT):
        digest.update(name.encode() + b"\0" + (ROOT / name).read_bytes() + b"\0")
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        commit, dirty = None, None  # Public source ZIPs deliberately omit Git history.
    return {
        "commit": commit,
        "dirty": dirty,
        "publicSourceSha256": digest.hexdigest(),
    }


def manifest(output, kind, **metadata):
    result = {
        "schema": 1,
        "kind": kind,
        "status": "candidate",
        "source": provenance(),
        "rulePolicy": "current-only-v1",
        "releaseReady": False,
        "manualAcceptance": "pending",
        **metadata,
        "files": {
            p.relative_to(output).as_posix(): file_hash(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    record(output / "MANIFEST.json", result)
    return result
