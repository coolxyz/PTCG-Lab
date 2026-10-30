"""Bind a candidate to the complete allowlisted current product source."""

from hashlib import sha256
from pathlib import Path

from scripts.public.policy import source_files, safe_file


def source_digest(root):
    root = Path(root)
    digest = sha256()
    for name in source_files(root):
        digest.update(name.encode() + b"\0" + safe_file(root, name).read_bytes() + b"\0")
    return digest.hexdigest()
