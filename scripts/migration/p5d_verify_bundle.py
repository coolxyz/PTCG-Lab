"""Verify local test kit integrity before installing. This is not a signature check."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath


def verify(root):
    root = Path(root)
    manifest = json.loads((root / "KIT-MANIFEST.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("stage") != "P5-D":
        raise ValueError("Unsupported kit manifest")
    for name, digest in manifest["files"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise ValueError("Unsafe kit path")
        file = root / name
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError("Linked kit input")
        if sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError("Test kit checksum mismatch")
    return {"status": "verified", "files": len(manifest["files"])}


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parent)))
