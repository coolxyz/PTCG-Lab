"""Export pinned Git artwork as verified Cloudflare Workers Static Assets.

Planning is offline. Building only writes a dedicated Git cache and a NEW output.
Only the explicit deploy command can publish; no App/profile/API is contacted.
"""

from __future__ import annotations

import argparse
from hashlib import sha1, sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.product.git_artwork import GitCache, app_root, encoded, regular_path  # noqa: E402

FREE_FILES = 20_000
CF_FILE_BYTES = 25 * 1024**2
APP_IMAGE_BYTES = 8 * 1024**2
DEFAULT_TOTAL_BYTES = 8 * 1024**3
HEADERS = b"/*\n  Cache-Control: public, max-age=31536000, immutable\n  X-Content-Type-Options: nosniff\n"
HEX40 = re.compile(r"[a-f0-9]{40}")
HEX64 = re.compile(r"[a-f0-9]{64}")
REPOSITORY = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9_-]*)/([A-Za-z0-9][A-Za-z0-9_.-]*)")
IMAGE_PATH = re.compile(r"img/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)")
MIMES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def checked_path(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("Paths must not contain symlinks or junctions")
    return path


def read_bytes(path, limit):
    path = checked_path(path)
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit:
        raise ValueError("Missing, linked or oversized input file")
    return path.read_bytes()


def parsed(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


def digest(raw):
    return sha256(raw).hexdigest()


def blob_id(raw):
    return sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def worker_config(name):
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        raise ValueError("Worker name must be 1-63 lowercase letters/digits/hyphens, starting with a letter")
    return {
        "name": name,
        "compatibility_date": "2026-10-30",
        "workers_dev": True,
        "assets": {"directory": "./public", "html_handling": "none", "not_found_handling": "none"},
    }


def image_mime(raw):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("Image format/pixel limit")
            mime = Image.MIME[image.format]
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
        return mime
    except Exception as error:
        raise ValueError("Artwork failed full image decoding") from error


def snapshot_indexes(home, *, include_variants=False):
    """Read public display metadata only; never import a frozen runtime or open user DBs."""
    home = checked_path(home)
    pointer = parsed(read_bytes(home / "current.json", 8192))
    release = pointer.get("releaseId")
    if not isinstance(release, str) or not HEX64.fullmatch(release):
        raise ValueError("Invalid current sync release")
    root = home / "releases" / release
    manifest = parsed(read_bytes(root / "manifest.json", 8 * 1024**2))
    raw = read_bytes(root / "data/catalog/catalog.json", 96 * 1024**2)
    if manifest.get("releaseId") != release or manifest.get("files", {}).get("data/catalog/catalog.json") != digest(raw):
        raise ValueError("Current sync catalogue differs from its release manifest")
    catalog = parsed(raw)
    catalog_pin = digest(raw)
    groups, image_maps = {}, {}
    for card in catalog["cards"]:
        images = card.get("images", [])
        if not include_variants:
            images = next(([image] for locale in ("zh-Hans", "zh-Hant") for image in images
                           if image.get("locale") == locale and image.get("equivalenceReviewed") is True
                           and image.get("source") and image.get("url")), [])
        else:
            images = [*images, *(v.get("image", {}) for v in card.get("variants", []))]
        for image in images:
            if image.get("equivalenceReviewed") is not True:
                continue
            match = re.fullmatch(r"/api/sync/images/([a-f0-9]{40})/([0-9]+)", image.get("url") or "")
            if not match:
                continue
            commit, card_id = match.groups()
            if commit not in image_maps:
                snapshot = home / "snapshots" / commit
                source = parsed(read_bytes(snapshot / "source.json", 24 * 1024**2))
                repository = source.get("repository")
                if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
                    raise ValueError("Invalid sync image repository")
                image_maps[commit] = (repository, parsed(read_bytes(snapshot / "image-index.json", 16 * 1024**2)))
            repository, rows = image_maps[commit]
            entry = rows.get(card_id)
            if not isinstance(entry, dict) or not entry.get("path") or not entry.get("blob"):
                raise ValueError("Current display image has no pinned path/blob; refresh sync metadata")
            key = (repository, commit)
            index = groups.setdefault(key, {"schema": 1, "repository": repository, "commit": commit,
                                           "catalogFileSha256": catalog_pin, "cards": {}})
            identity = card["printingId"] + (":" + card_id if include_variants else "")
            index["cards"][identity] = {"printingId": identity, "engineId": card["engineId"],
                                        "path": entry["path"], "blob": entry["blob"]}
    return [{**index, "cards": list(index["cards"].values())} for index in groups.values()]


def plan(indexes, *, name="ptcg-card-images", max_files=FREE_FILES, sync_home=None, include_variants=False):
    worker_config(name)
    if type(max_files) is not int or not 2 <= max_files <= FREE_FILES:
        raise ValueError("File budget must be between 2 and the 20,000-file Free limit")
    sources, images, entries = {}, {}, 0
    inputs = [*indexes, *(snapshot_indexes(sync_home, include_variants=include_variants) if sync_home else [])]
    for path in inputs:
        raw = encoded(path) if isinstance(path, dict) else read_bytes(path, 8 * 1024**2)
        index = parsed(raw)
        if not isinstance(index, dict) or set(index) != {"schema", "repository", "commit", "catalogFileSha256", "cards"}:
            raise ValueError("Invalid artwork index schema")
        repository = index["repository"]
        match = REPOSITORY.fullmatch(repository) if isinstance(repository, str) else None
        commit = index["commit"]
        if (type(index["schema"]) is not int or index["schema"] != 1 or not match
                or not isinstance(commit, str) or not HEX40.fullmatch(commit)
                or not isinstance(index["catalogFileSha256"], str)
                or not HEX64.fullmatch(index["catalogFileSha256"])
                or not isinstance(index["cards"], list) or not 1 <= len(index["cards"]) <= 100000):
            raise ValueError("Invalid pinned artwork index")
        pin = digest(raw)
        if pin in sources:
            continue
        sources[pin] = {k: index[k] for k in ("repository", "commit", "catalogFileSha256")}
        sources[pin].update(indexSha256=pin, cards=len(index["cards"]))
        seen = set()
        for row in index["cards"]:
            if not isinstance(row, dict) or set(row) != {"printingId", "engineId", "path", "blob"}:
                raise ValueError("Invalid artwork index row")
            if (not all(isinstance(row[k], str) and row[k].strip() for k in row)
                    or row["printingId"] in seen or not HEX40.fullmatch(row["blob"])
                    or not IMAGE_PATH.fullmatch(row["path"])):
                raise ValueError("Invalid, duplicate or unsafe artwork row")
            regular_path(row["path"])
            seen.add(row["printingId"])
            destination = "/".join((*match.groups(), commit, row["path"]))
            entry = {"repository": repository, "commit": commit, "path": row["path"], "blob": row["blob"]}
            if destination in images and images[destination] != entry:
                raise ValueError("Conflicting immutable image URL")
            images[destination] = entry
            entries += 1
    if not images:
        raise ValueError("No indexed artwork selected")
    # Conservatively reserve one file for _headers even if the platform excludes it.
    if len(images) + 1 > max_files:
        raise ValueError(f"Free file budget exceeded: {len(images)} images + 1 control file > {max_files}; select fewer indexes")
    return {
        "schema": 1, "kind": "ptcg-cloudflare-artwork-plan", "workerName": name,
        "sources": sorted(sources.values(), key=lambda s: s["indexSha256"]),
        "indexedCards": entries, "images": dict(sorted(images.items())),
        "filesWithControlReserve": len(images) + 1, "maxFiles": max_files,
        "imageDownloads": 0,
    }


def build(indexes, cache_path, output, *, name="ptcg-card-images", previous=None,
          max_files=FREE_FILES, max_total_bytes=DEFAULT_TOTAL_BYTES, cache_factory=GitCache,
          sync_home=None, include_variants=False):
    selection = plan(indexes, name=name, max_files=max_files, sync_home=sync_home, include_variants=include_variants)
    if type(max_total_bytes) is not int or max_total_bytes <= 0:
        raise ValueError("Total disk budget must be positive")
    output, cache_path = checked_path(output), checked_path(cache_path)
    if output.exists():
        raise ValueError("Choose a NEW output directory; existing releases are never overwritten")
    if output.is_relative_to(ROOT) and output.relative_to(ROOT).parts[0] not in {"var", "release", "exports"}:
        raise ValueError("Generated output in this repository belongs under var/, release/ or exports/")
    protected = [cache_path, *[checked_path(p) for p in indexes]]
    if sync_home:
        protected.append(checked_path(sync_home))
    old = {}
    if previous:
        previous = checked_path(previous)
        verified = verify(previous)
        protected.append(previous)
        for dest, row in verified["files"].items():
            if "blob" in row:
                old.setdefault(row["blob"], previous / "public" / dest)
    if any(output == p or output.is_relative_to(p) or p.is_relative_to(output) for p in protected):
        raise ValueError("Output must be separate from indexes, Git cache and previous output")
    output.parent.mkdir(parents=True, exist_ok=True)
    records, total, reused, downloaded = {}, 0, 0, 0
    with tempfile.TemporaryDirectory(prefix=".ptcg-cdn-", dir=output.parent) as temporary:
        stage = Path(temporary)
        public = stage / "public"
        public.mkdir()
        (public / "_headers").write_bytes(HEADERS)
        rows = list(selection["images"].items())
        caches = {}
        for source in selection["sources"]:
            repository, commit = source["repository"], source["commit"]
            if (repository, commit) in caches:
                continue
            cache = cache_factory(cache_path / digest(repository.encode())[:24], repository)
            if cache.fetch(commit) != commit:
                raise ValueError("Pinned commit mismatch")
            tree = cache.tree(commit)
            for _, row in rows:
                if row["repository"] == repository and row["commit"] == commit and tree.get(row["path"]) != row["blob"]:
                    raise ValueError("Artwork index differs from the pinned Git tree")
            caches[repository, commit] = cache
        for start in range(0, len(rows), 32):
            batch = rows[start:start + 32]
            for key, cache in caches.items():
                cache.prefetch([row["blob"] for _, row in batch
                                if (row["repository"], row["commit"]) == key and row["blob"] not in old])
            for dest, row in batch:
                if row["blob"] in old:
                    raw = read_bytes(old[row["blob"]], APP_IMAGE_BYTES)
                    reused += 1
                else:
                    raw = caches[row["repository"], row["commit"]].blob(row["blob"], APP_IMAGE_BYTES)
                    downloaded += 1
                if not raw or len(raw) > min(APP_IMAGE_BYTES, CF_FILE_BYTES) or blob_id(raw) != row["blob"]:
                    raise ValueError("Artwork size or pinned Git blob mismatch")
                mime = image_mime(raw)
                if mime != MIMES[Path(dest).suffix]:
                    raise ValueError("Image extension would give an incorrect CDN Content-Type")
                total += len(raw)
                if total > max_total_bytes:
                    raise ValueError("CDN export exceeds local disk budget; Git cache retained")
                target = public / dest
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                records[dest] = {"sha256": digest(raw), "size": len(raw), "blob": row["blob"], "mime": mime}
                old.setdefault(row["blob"], target)
            print(f"Verified {min(start + 32, len(rows))}/{len(rows)} CDN images", flush=True)
        records["_headers"] = {"sha256": digest(HEADERS), "size": len(HEADERS)}
        config = encoded(worker_config(name)) + b"\n"
        (stage / "wrangler.json").write_bytes(config)
        manifest = {
            "schema": 1, "kind": "ptcg-cloudflare-artwork", "workerName": name,
            "sources": selection["sources"], "files": dict(sorted(records.items())),
            "wranglerSha256": digest(config), "maxFiles": max_files,
            "imageBytes": total, "filesWithControlReserve": len(records),
        }
        raw_manifest = encoded(manifest) + b"\n"
        (stage / "MANIFEST.json").write_bytes(raw_manifest)
        (stage / "MANIFEST.sha256").write_text(digest(raw_manifest) + "\n", encoding="ascii")
        (stage / "PLAN.json").write_bytes(encoded(selection) + b"\n")
        report = {
            "status": "ready-for-manual-deploy", "images": len(rows), "imageBytes": total,
            "reusedFiles": reused, "gitBlobReads": downloaded,
            "manifestSha256": digest(raw_manifest), "automaticallyDeployed": False,
            "appImportRequired": False,
        }
        (stage / "REPORT.json").write_bytes(encoded(report) + b"\n")
        verify(stage, pin=digest(raw_manifest))
        # Reserve the destination exclusively: a concurrently created output is never replaced.
        output.mkdir()
        try:
            for item in stage.iterdir():
                shutil.move(str(item), output / item.name)
        except BaseException:
            shutil.rmtree(output)
            raise
    return report


def verify(directory, *, pin=None):
    directory = checked_path(directory)
    raw = read_bytes(directory / "MANIFEST.json", 16 * 1024**2)
    if pin is not None and (not isinstance(pin, str) or not HEX64.fullmatch(pin) or digest(raw) != pin):
        raise ValueError("Manifest SHA-256 mismatch")
    manifest = parsed(raw)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema", "kind", "workerName", "sources", "files", "wranglerSha256", "maxFiles", "imageBytes", "filesWithControlReserve"
    } or manifest["schema"] != 1 or manifest["kind"] != "ptcg-cloudflare-artwork":
        raise ValueError("Invalid CDN manifest")
    expected_config = encoded(worker_config(manifest["workerName"])) + b"\n"
    config = read_bytes(directory / "wrangler.json", 8192)
    if config != expected_config or digest(config) != manifest["wranglerSha256"]:
        raise ValueError("Wrangler configuration changed or gained non-static capabilities")
    if (read_bytes(directory / "MANIFEST.sha256", 128).decode("ascii").strip() != digest(raw)
            or type(manifest["maxFiles"]) is not int or not 2 <= manifest["maxFiles"] <= FREE_FILES
            or not isinstance(manifest["files"], dict)
            or len(manifest["files"]) != manifest["filesWithControlReserve"]
            or not 2 <= len(manifest["files"]) <= manifest["maxFiles"]):
        raise ValueError("Invalid CDN file budget/checksum")
    public = checked_path(directory / "public")
    actual = set()
    for p in public.rglob("*"):
        checked_path(p)
        if not p.is_dir():
            actual.add(p.relative_to(public).as_posix())
    if actual != set(manifest["files"]):
        extras, missing = sorted(actual - set(manifest["files"])), sorted(set(manifest["files"]) - actual)
        raise ValueError("Unexpected or missing public assets: " + json.dumps({
            "unexpectedCount": len(extras), "unexpectedExamples": extras[:5],
            "missingCount": len(missing), "missingExamples": missing[:5],
        }, ensure_ascii=True) + ("; Finder metadata must be moved outside public/ before retrying" if any(Path(x).name == ".DS_Store" for x in extras) else ""))
    total = 0
    for dest, row in manifest["files"].items():
        regular_path(dest)
        if not isinstance(row, dict):
            raise ValueError("Invalid CDN file record")
        raw_file = read_bytes(public / dest, APP_IMAGE_BYTES)
        if dest == "_headers":
            if raw_file != HEADERS or set(row) != {"sha256", "size"}:
                raise ValueError("Unexpected CDN header policy")
        else:
            parts = dest.split("/", 3)
            if (len(parts) != 4 or not REPOSITORY.fullmatch("https://github.com/" + "/".join(parts[:2]))
                    or not HEX40.fullmatch(parts[2]) or not IMAGE_PATH.fullmatch(parts[3])
                    or set(row) != {"sha256", "size", "blob", "mime"}
                    or blob_id(raw_file) != row["blob"] or image_mime(raw_file) != row["mime"]
                    or row["mime"] != MIMES[Path(dest).suffix]):
                raise ValueError("Invalid CDN image identity")
            total += len(raw_file)
        if row["sha256"] != digest(raw_file) or row["size"] != len(raw_file):
            raise ValueError("CDN file content changed")
    if total != manifest["imageBytes"]:
        raise ValueError("CDN byte inventory mismatch")
    return manifest


def deploy(directory, *, pin, account_id, wrangler, dry_run=False):
    manifest = verify(directory, pin=pin)
    if not re.fullmatch(r"[a-f0-9]{32}", account_id):
        raise ValueError("Supply the explicit 32-character Cloudflare account ID")
    tool = checked_path(wrangler)
    if not tool.is_file():
        raise ValueError("Supply an installed Wrangler executable or bin/wrangler.js")
    if tool.suffix.lower() in {".bat", ".cmd", ".ps1"}:
        raise ValueError("On Windows select node_modules/wrangler/bin/wrangler.js, not a shell wrapper")
    command = ([shutil.which("node") or "node", str(tool)] if tool.suffix == ".js" else [str(tool)])
    command += ["deploy", "--config", str(checked_path(directory) / "wrangler.json")]
    if dry_run:
        return {"status": "dry-run", "workerName": manifest["workerName"], "command": command, "published": False}
    env = {**os.environ, "CLOUDFLARE_ACCOUNT_ID": account_id, "WRANGLER_SEND_METRICS": "false"}
    subprocess.run(command, cwd=checked_path(directory), env=env, stdin=subprocess.DEVNULL, check=True)
    return {"status": "wrangler-completed", "workerName": manifest["workerName"], "published": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("plan", "build"):
        p = commands.add_parser(operation)
        source = p.add_mutually_exclusive_group()
        source.add_argument("--index", type=Path, action="append", help="Exact App artwork index; repeat to retain multiple commits")
        source.add_argument("--app", type=Path, help="Read the index from an installed macOS App or unpacked Windows bundle")
        p.add_argument("--name", default="ptcg-card-images")
        p.add_argument("--max-files", type=int, default=FREE_FILES)
        p.add_argument("--sync-home", type=Path, help="Explicit sync directory; include current catalogue's preferred display images without reading credentials")
        p.add_argument("--include-variants", action="store_true", help="Also include other reviewed card faces from --sync-home; may exceed Free file budget")
        if operation == "build":
            p.add_argument("--cache", type=Path, required=True)
            p.add_argument("--output", type=Path, required=True)
            p.add_argument("--previous", type=Path)
            p.add_argument("--max-total-mib", type=int, default=DEFAULT_TOTAL_BYTES // 1024**2)
    for operation in ("verify", "deploy"):
        p = commands.add_parser(operation)
        p.add_argument("--directory", type=Path, required=True)
        p.add_argument("--manifest-sha256", required=True)
        if operation == "deploy":
            p.add_argument("--account-id", required=True)
            p.add_argument("--wrangler", type=Path, required=True)
            p.add_argument("--dry-run", action="store_true", help="Print the verified command without starting Wrangler")
    args = parser.parse_args()
    if args.command in {"plan", "build"}:
        if args.include_variants and not args.sync_home:
            raise ValueError("--include-variants requires --sync-home")
        indexes = args.index or [(app_root(args.app) if args.app else ROOT) / "resources/artwork/builtin-index.json"]
        if args.command == "plan":
            selection = plan(indexes, name=args.name, max_files=args.max_files, sync_home=args.sync_home, include_variants=args.include_variants)
            result = {k: v for k, v in selection.items() if k != "images"}
            result["images"] = len(selection["images"])
        else:
            result = build(indexes, args.cache, args.output, name=args.name, previous=args.previous,
                           max_files=args.max_files, max_total_bytes=args.max_total_mib * 1024**2,
                           sync_home=args.sync_home, include_variants=args.include_variants)
    elif args.command == "verify":
        value = verify(args.directory, pin=args.manifest_sha256)
        result = {"status": "verified", "files": len(value["files"]), "imageBytes": value["imageBytes"]}
    else:
        result = deploy(args.directory, pin=args.manifest_sha256, account_id=args.account_id,
                        wrangler=args.wrangler, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
