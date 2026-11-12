"""Build a Docker-only in-place updater from an accepted image and its predecessor."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from scripts.packaging.common import file_hash, new_output
from scripts.packaging.offline_server import archive_identity


def build(update, previous, output):
    update, previous = Path(update), Path(previous)
    new = json.loads((update / "UPDATE.json").read_bytes())
    old = json.loads(previous.read_bytes())
    source = update / "api-image.tar.gz"
    if (new["platform"] != old["platform"] or new["platform"] not in {"linux/arm64", "linux/amd64"}
            or new["kind"] != "ptcg-api-image-update" or new["schema"] != 1
            or file_hash(source) != new["imageArchiveSha256"]):
        raise ValueError("Update and predecessor manifests or image archive differ.")
    image = new["image"]
    if archive_identity(source, image["tag"], new["platform"], image["id"]) != image:
        raise ValueError("Update archive identity differs.")
    commit = new["upstreamCommit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a pinned upstream commit.")
    for data in (new, old):
        if not re.fullmatch(r"rules-core-v2-[0-9a-f]{64}", data["ruleReleaseId"]) or not re.fullmatch(
            r"[0-9a-f]{64}", data["ruleManifestSha256"]
        ):
            raise ValueError("Expected pinned execution and manifest identities.")
    output = new_output(output)
    # Rule-only releases can follow an update with the same upstream commit.
    update_id = new["ruleManifestSha256"][:12] if old.get("upstreamCommit") == commit else commit[:8]
    constants = {
        "UPDATE_ID": update_id, "PLATFORM": new["platform"], "IMAGE": image["tag"],
        "IMAGE_ID": image["id"], "IMAGE_MANIFEST_ID": image["manifestId"],
        "OLD_RULE": old["ruleReleaseId"], "OLD_PIN": old["ruleManifestSha256"],
        "RULE": new["ruleReleaseId"], "PIN": new["ruleManifestSha256"],
        "CATALOG": new["displayCatalogVersion"],
    }
    files = {
        source: "api-image.tar.gz", update / "UPDATE.json": "UPDATE.json",
        ROOT / "scripts/packaging/api-update.sh": "update.sh",
        ROOT / "scripts/packaging/api-update-config.py": "update-config.py",
        ROOT / "scripts/packaging/api-update-README.md": "README.md",
    }
    for path, name in files.items():
        shutil.copyfile(path, output / name)
    (output / "update.sh").chmod(0o755)
    (output / "update.env").write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in constants.items()))
    (output / "SHA256SUMS").write_text("".join(
        f"{file_hash(path)}  {path.name}\n" for path in sorted(output.iterdir())
    ))
    archive = output.with_suffix(".tar.gz")
    with tarfile.open(archive, "x:gz", compresslevel=1) as target:
        target.add(output, arcname=output.name)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".gz.sha256").write_text(f"{checksum}  {archive.name}\n")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("update", "previous", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(build(args.update, args.previous, args.output))
