"""Transfer explicitly selected, commit-pinned artwork without copying user databases.

Run with the APP stopped when installing. Existing versions are retained. Pack files
are integrity checked, not signed: accept them only from an approved publisher.
"""

import argparse
from hashlib import sha1, sha256
import io
import json
from pathlib import Path
import re
import sqlite3
import uuid
from contextlib import closing
from PIL import Image

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
MAX_IMAGE = 8 * 1024 * 1024


def regular(root, relative):
    path = root / relative
    if (
        not path.resolve().is_relative_to(root.resolve())
        or any(
            p.is_symlink()
            for p in (path, *path.parents)
            if p != root and p.is_relative_to(root)
        )
        or not path.is_file()
    ):
        raise ValueError("Missing or linked resource input")
    return path


def image_info(data, blob):
    if (
        len(data) > MAX_IMAGE
        or sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() != blob
    ):
        raise ValueError("Artwork does not match the pinned Git blob")
    with Image.open(io.BytesIO(data)) as image:
        if (
            image.format not in ("PNG", "JPEG", "WEBP")
            or image.width * image.height > 20_000_000
        ):
            raise ValueError("Invalid artwork")
        mime = Image.MIME[image.format]
        image.verify()
    return sha256(data).hexdigest(), mime


def export_pack(home, output):
    home, output = Path(home).resolve(), Path(output).absolute()
    if output.exists():
        raise ValueError("Choose a new resource pack directory")
    records, blobs = [], {}
    database = regular(home, "images/index.sqlite")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT commit_id,card_id,sha FROM images ORDER BY commit_id,card_id"
        ).fetchall()
    indexes = {}
    for commit, card, digest in rows:
        if (
            not HEX40.fullmatch(commit)
            or not re.fullmatch(r"[0-9]{1,20}", card)
            or not HEX64.fullmatch(digest)
        ):
            raise ValueError("Invalid cached image identity")
        if commit not in indexes:
            indexes[commit] = json.loads(
                regular(home, f"snapshots/{commit}/image-index.json").read_bytes()
            )
        entry = indexes[commit][card]
        data = regular(home, "images/" + digest).read_bytes()
        actual, mime = image_info(data, entry["blob"])
        if actual != digest:
            raise ValueError("Corrupt source cache")
        # Never export configured repository URLs, local paths, SQLite or user uploads.
        records.append(
            {
                "commit": commit,
                "card": card,
                "sha256": digest,
                "blob": entry["blob"],
                "mime": mime,
            }
        )
        blobs[digest] = data
    output.mkdir(parents=True)
    (output / "images").mkdir()
    for digest, data in blobs.items():
        (output / "images" / digest).write_bytes(data)
    manifest = {"schema": 1, "kind": "ptcg-approved-artwork", "images": records}
    (output / "RESOURCES.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return {"images": len(records), "bytes": sum(map(len, blobs.values()))}


def install_pack(pack, home):
    pack, home = Path(pack).resolve(), Path(home).absolute()
    manifest_path = regular(pack, "RESOURCES.json")
    if manifest_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Resource manifest too large")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema") != 1 or manifest.get("kind") != "ptcg-approved-artwork":
        raise ValueError("Unsupported resource pack")
    validated, seen = [], set()
    total = 0
    for row in manifest["images"]:
        commit, card, digest = row["commit"], row["card"], row["sha256"]
        if (
            not HEX40.fullmatch(commit)
            or not re.fullmatch(r"[0-9]{1,20}", card)
            or not HEX64.fullmatch(digest)
            or (commit, card) in seen
        ):
            raise ValueError("Invalid or repeated resource identity")
        seen.add((commit, card))
        source = regular(pack, "images/" + digest)
        total += source.stat().st_size
        if source.stat().st_size > MAX_IMAGE or total > 256 * 1024 * 1024:
            raise ValueError("Resource pack exceeds transfer budget (256 MiB)")
        data = source.read_bytes()
        actual, mime = image_info(data, row["blob"])
        if actual != digest or mime != row["mime"]:
            raise ValueError("Resource checksum mismatch")
        validated.append((row, data))
    # Validate the whole pack before making changes. Never replace an existing version.
    if any(p.is_symlink() for p in (home, *home.parents)):
        raise ValueError("Linked resource destination")
    directory = home / "images"
    if directory.is_symlink() or (directory / "index.sqlite").is_symlink():
        raise ValueError("Linked cache destination")
    indexes = {}
    for row, _ in validated:
        path = home / "snapshots" / row["commit"] / "image-index.json"
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(home)):
            raise ValueError("Linked snapshot destination")
        if row["commit"] not in indexes:
            indexes[row["commit"]] = (
                json.loads(path.read_bytes()) if path.exists() else {}
            )
        index = indexes[row["commit"]]
        if row["card"] in index and index[row["card"]].get("blob") != row["blob"]:
            raise ValueError("Existing snapshot conflicts with resource pack")
        index.setdefault(row["card"], {"blob": row["blob"], "path": None})
    directory.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(directory / "index.sqlite")) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS images(commit_id TEXT, card_id TEXT, sha TEXT, mime TEXT, PRIMARY KEY(commit_id,card_id))"
        )
        for row, data in validated:
            old = db.execute(
                "SELECT sha,mime FROM images WHERE commit_id=? AND card_id=?",
                (row["commit"], row["card"]),
            ).fetchone()
            target = directory / row["sha256"]
            if old and old != (row["sha256"], row["mime"]):
                raise ValueError(
                    "Existing pinned version conflicts; keep it and inspect the pack"
                )
            if target.is_symlink() or (target.exists() and target.read_bytes() != data):
                raise ValueError("Existing artwork is corrupt or linked")
        for commit, index in indexes.items():
            path = home / "snapshots" / commit / "image-index.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name("image-index-" + uuid.uuid4().hex + ".tmp")
            temp.write_text(json.dumps(index) + "\n")
            temp.replace(path)
        for row, data in validated:
            target = directory / row["sha256"]
            if not target.exists():
                with target.open("xb") as file:
                    file.write(data)
            db.execute(
                "INSERT OR IGNORE INTO images VALUES(?,?,?,?)",
                (row["commit"], row["card"], row["sha256"], row["mime"]),
            )
    return {"installed": len(validated), "retainedOldVersions": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "install"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            export_pack(args.home, args.pack)
            if args.action == "export"
            else install_pack(args.pack, args.home)
        )
    )
