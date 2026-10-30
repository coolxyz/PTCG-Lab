"""Offline, same-user desktop installation transactions; never an HTTP API.

The profile path stays fixed (Keychain/DPAPI bind credentials to that path).
One durable maintenance marker blocks startup while multiple files are switched.
An interrupted switch requires explicit finish/abort, never automatic rollback.
The POSIX installer is implemented first; Windows execution remains pending.
"""

from contextlib import contextmanager
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from ptcg_desktop_api.sealed_file import access  # noqa: E402
from ptcg_desktop_api.installation import (  # noqa: E402
    RECORD,
    MARKER,
    SKIP,
    InstallError,
    fail,
    regular,
    digest,
    read,
    payload_record,
)

MAX_BYTES = 8 * 1024**3
MAX_FILES = 100000


def private(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        fail("INSTALL_UNSAFE_PATH")
    # Persist each new directory entry before a journal can refer to it.
    # A leaf fsync alone does not persist its entry in its own parent.
    for directory in reversed((path, *path.parents)):
        if not directory.exists():
            directory.mkdir(mode=0o700)
            sync_dir(directory.parent)
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        fail("INSTALL_PRIVATE_DIRECTORY_REQUIRED")
    return path


def owned_directory(boundary, path):
    """Private outer boundary protects existing product subdirectories too.

    Existing 0755 sync/cache folders inside a 0700 profile are not public. Do
    not repair their permissions; newly created descendants are always 0700.
    """
    boundary, path = private(boundary), Path(path).absolute()
    current = boundary
    for part in path.relative_to(boundary).parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            fail("INSTALL_UNSAFE_PATH")
        if not current.exists():
            current.mkdir(mode=0o700)
            sync_dir(current.parent)
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            fail("INSTALL_UNSAFE_PATH")
    return path


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, value):
    path = Path(path)
    private(path.parent)
    if path.exists() or path.is_symlink():
        regular(path)
    tmp = path.parent / (".write-" + uuid.uuid4().hex)
    access(tmp, data=(json.dumps(value, sort_keys=True) + "\n").encode())
    os.replace(tmp, path)
    sync_dir(path.parent)


def tree(root, *, profile=False):
    root = Path(root)
    result, size = {}, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for p in sorted(directory.iterdir()):
            name = p.relative_to(root).as_posix()
            if profile and p.relative_to(root).parts[0] in SKIP:
                continue
            if p.is_symlink() or p.is_junction():
                fail("INSTALL_UNSAFE_PATH")
            info = p.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(p)
            else:
                regular(p)
                size += info.st_size
                if size > MAX_BYTES or len(result) >= MAX_FILES:
                    fail("INSTALL_CAPACITY")
                result[name] = {
                    "sha256": digest(p),
                    "size": info.st_size,
                    "executable": bool(info.st_mode & 0o111),
                }
    return result


def tree_id(files):
    return sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def bundle(app):
    app = Path(app).absolute()
    files = tree(app)
    payload = app / "Contents/Resources/backend"
    record = payload_record(payload)
    manifest = json.loads(regular(payload / "manifest.json").read_bytes())
    if manifest.get("schema") != 1 or manifest.get("platform") != "macos-arm64":
        fail("INSTALL_PLATFORM_PENDING")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected or len(expected) > 20000:
        fail("INSTALL_INVALID_BUNDLE")
    actual = {
        n.removeprefix("Contents/Resources/backend/"): f["sha256"]
        for n, f in files.items()
        if n.startswith("Contents/Resources/backend/")
        and n != "Contents/Resources/backend/manifest.json"
    }
    if actual != expected or "Contents/MacOS/ptcg-native-shell" not in files:
        fail("INSTALL_INVALID_BUNDLE")
    return {"bundleSha256": tree_id(files), "deployment": record, "files": files}


def copy_tree(source, destination, files):
    private(destination)
    for name, entry in files.items():
        # Inventories are produced internally or authenticated by tree_id.
        if (
            Path(name).as_posix() != name
            or Path(name).is_absolute()
            or any(p in {"..", "."} for p in Path(name).parts)
            or "\\" in name
            or ":" in name
        ):
            fail("INSTALL_INVALID_RECORD")
        src, dst = Path(source) / name, Path(destination) / name
        if digest(src) != entry["sha256"]:
            fail("INSTALL_SNAPSHOT_CHANGED")
        owned_directory(destination, dst.parent)
        with regular(src).open("rb") as inp, dst.open("xb") as out:
            shutil.copyfileobj(inp, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        dst.chmod(0o700 if entry["executable"] else 0o600)
        sync_dir(dst.parent)


def check_databases(snapshot):
    # Validate private scratch copies, so SQLite recovery never writes into the
    # immutable rollback snapshot. WAL bytes are included in these copies.
    for db in Path(snapshot).rglob("*.sqlite"):
        with tempfile.TemporaryDirectory(prefix="ptcg-install-db-") as temp:
            target = Path(temp) / "check.sqlite"
            shutil.copyfile(db, target)
            for suffix in ("-wal", "-shm"):
                side = Path(str(db) + suffix)
                if side.exists():
                    shutil.copyfile(regular(side), str(target) + suffix)
            with sqlite3.connect(target) as connection:
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    fail("INSTALL_DATABASE_INVALID")
                if db.name == "app.sqlite" and connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0] not in {0, 1}:
                    fail("DESKTOP_SCHEMA_UNSUPPORTED")


@contextmanager
def offline(root, state):
    if sys.platform != "darwin":
        fail("INSTALL_PLATFORM_PENDING")
    from ptcg_online_client.vault import PosixFiles
    from ptcg_desktop_api.native import StateLock

    root, state = private(root), private(state)
    if root == state or root.is_relative_to(state) or state.is_relative_to(root):
        fail("INSTALL_OVERLAPPING_DIRECTORIES")
    locks = []
    try:
        # The shell lock prevents a new window racing the native backend lock.
        locks.append(PosixFiles(root, "installer"))
        locks.append(PosixFiles(state, "shell"))
        locks.append(StateLock(state))
        yield root, state
    finally:
        for lock in reversed(locks):
            lock.close()


class Installer:
    def __init__(self, root, state):
        self.root, self.state = Path(root).absolute(), Path(state).absolute()

    def active(self):
        path = self.root / "active.json"
        if not path.exists():
            return None
        value = read(path)
        if (
            set(value) != {"schema", "profile", "bundleSha256", "deployment"}
            or value["schema"] != 1
            or value["profile"] != str(self.state)
        ):
            fail("INSTALL_PROFILE_MISMATCH")
        self.version(value["bundleSha256"])
        return value

    def version(self, key):
        if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key):
            fail("INSTALL_INVALID_RECORD")
        return self.root / "versions" / key / "PTCG Lab Preview.app"

    def verify_version(self, active):
        found = bundle(self.version(active["bundleSha256"]))
        if (
            found["bundleSha256"] != active["bundleSha256"]
            or found["deployment"] != active["deployment"]
        ):
            fail("INSTALL_VERSION_CHANGED")

    def save_snapshot(self, tx, name):
        files = tree(self.state, profile=True)
        copy_tree(self.state, tx / name, files)
        check_databases(tx / name)
        return files

    def apply(self, tx, side, expected):
        src = tx / side
        if tree(src) != expected:
            fail("INSTALL_SNAPSHOT_CHANGED")
        actual = tree(self.state, profile=True)
        # Marker remains present throughout deletion/replacement. Lock files
        # are never replaced, so no writer can lock a detached old inode.
        for name in sorted(set(actual) - set(expected)):
            (self.state / name).unlink()
            sync_dir((self.state / name).parent)
        # Empty directory structure is not durable data. Remove only empty
        # directories, including interrupted staging, to permit file/dir changes.
        directories = [
            p
            for p in self.state.rglob("*")
            if p.is_dir() and p.relative_to(self.state).parts[0] not in SKIP
        ]
        for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                continue
            sync_dir(directory.parent)
        for name, entry in expected.items():
            target = self.state / name
            owned_directory(self.state, target.parent)
            if name in actual:
                regular(target)
            temp = target.parent / (".install-" + uuid.uuid4().hex)
            with regular(src / name).open("rb") as inp, temp.open("xb") as out:
                shutil.copyfileobj(inp, out, length=1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            temp.chmod(0o700 if entry["executable"] else 0o600)
            if digest(temp) != entry["sha256"]:
                fail("INSTALL_SNAPSHOT_CHANGED")
            os.replace(temp, target)
            sync_dir(target.parent)
        if tree(self.state, profile=True) != expected:
            fail("INSTALL_PROFILE_COPY_MISMATCH")

    def commit(self, tx, journal, side, fault=None):
        active = journal[side + "Active"]
        if active:
            self.verify_version(active)
        self.apply(tx, side, journal[side + "Files"])
        if fault:
            fault("profile-written")
        if active is None:
            (self.root / "active.json").unlink(missing_ok=True)
            sync_dir(self.root)
        else:
            write(self.root / "active.json", active)
        if fault:
            fault("pointer-written")
        (self.state / MARKER).unlink()
        sync_dir(self.state)

    def transaction(self, after, *, restore=None, adopt=False, fault=None):
        if (self.state / MARKER).exists():
            fail("DESKTOP_RECOVERY_REQUIRED")
        before = self.active()
        if before:
            self.verify_version(before)
            if read(self.state / RECORD) != before["deployment"]:
                fail("INSTALL_PROFILE_MISMATCH")
        elif any(p.name not in SKIP for p in self.state.iterdir()) and not adopt:
            fail("INSTALL_EXPLICIT_ADOPTION_REQUIRED")
        files = tree(self.state, profile=True)
        size = sum(v["size"] for v in files.values())
        if shutil.disk_usage(self.root).free < 3 * size + 32 * 1024**2:
            fail("INSTALL_DISK_SPACE")
        key = uuid.uuid4().hex
        tx = private(self.root / "transactions" / key)
        before_files = self.save_snapshot(tx, "before")
        if restore:
            copy_tree(restore[0], tx / "after", restore[1])
        else:
            copy_tree(tx / "before", tx / "after", before_files)
            write(tx / "after" / RECORD, after["deployment"])
        check_databases(tx / "after")
        journal = {
            "schema": 1,
            "profile": str(self.state),
            "beforeActive": before,
            "afterActive": after,
            "beforeFiles": before_files,
            "afterFiles": tree(tx / "after"),
        }
        write(tx / "transaction.json", journal)
        write(
            self.state / MARKER,
            {
                "schema": 1,
                "root": str(self.root),
                "transaction": key,
                "journalSha256": digest(tx / "transaction.json"),
            },
        )
        if fault:
            fault("prepared")
        self.commit(tx, journal, "after", fault)
        return {
            "status": "installed",
            "transaction": key,
            "bundleSha256": after["bundleSha256"] if after else None,
            "releaseReady": False,
        }

    def install(self, candidate, expected, *, adopt=False, fault=None):
        with offline(self.root, self.state):
            info = bundle(candidate)
            if info["bundleSha256"] != expected:
                fail("INSTALL_DIGEST_MISMATCH")
            destination = self.version(expected)
            if not destination.exists():
                stage = private(self.root / (".stage-" + uuid.uuid4().hex))
                copy_tree(candidate, stage / "PTCG Lab Preview.app", info["files"])
                if bundle(stage / "PTCG Lab Preview.app") != info:
                    fail("INSTALL_COPY_MISMATCH")
                private(destination.parent.parent)
                os.replace(stage, destination.parent)
                sync_dir(destination.parent.parent)
            active = {
                "schema": 1,
                "profile": str(self.state),
                "bundleSha256": expected,
                "deployment": info["deployment"],
            }
            self.verify_version(active)
            if self.active() == active:
                fail("INSTALL_ALREADY_CURRENT")
            return self.transaction(active, adopt=adopt, fault=fault)

    def journal(self, key):
        if not isinstance(key, str) or not re.fullmatch("[a-f0-9]{32}", key):
            fail("INSTALL_INVALID_RECORD")
        tx = self.root / "transactions" / key
        j = read(tx / "transaction.json")
        if j.get("schema") != 1 or j.get("profile") != str(self.state):
            fail("INSTALL_PROFILE_MISMATCH")
        return tx, j

    def recover(self, mode):
        if mode not in {"finish", "abort"}:
            fail("INSTALL_INVALID_RECOVERY")
        with offline(self.root, self.state):
            marker = read(self.state / MARKER)
            if marker.get("schema") != 1 or marker.get("root") != str(self.root):
                fail("INSTALL_PROFILE_MISMATCH")
            tx, journal = self.journal(marker["transaction"])
            if digest(tx / "transaction.json") != marker["journalSha256"]:
                fail("INSTALL_SNAPSHOT_CHANGED")
            self.commit(tx, journal, "after" if mode == "finish" else "before")
            return {"status": "recovered", "mode": mode, "releaseReady": False}

    def rollback(self, key, *, expected, fault=None):
        with offline(self.root, self.state):
            current = self.active()
            if current is None or current["bundleSha256"] != expected:
                fail("INSTALL_STALE_ACTIVE")
            tx, journal = self.journal(key)
            old = journal["beforeActive"]
            if old is None or journal["afterActive"] != current:
                fail("INSTALL_ROLLBACK_PAIR_REQUIRED")
            self.verify_version(old)
            if tree(tx / "before") != journal["beforeFiles"]:
                fail("INSTALL_SNAPSHOT_CHANGED")
            return self.transaction(
                old, restore=(tx / "before", journal["beforeFiles"]), fault=fault
            )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    commands = p.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--candidate", type=Path, required=True)
    install.add_argument("--sha256", required=True)
    install.add_argument("--adopt", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--transaction", required=True)
    rollback.add_argument("--expected", required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("--mode", choices=["finish", "abort"], required=True)
    commands.add_parser("status")
    args = p.parse_args(argv)
    i = Installer(args.root, args.state_dir)
    try:
        if args.command == "install":
            result = i.install(args.candidate, args.sha256, adopt=args.adopt)
        elif args.command == "rollback":
            result = i.rollback(args.transaction, expected=args.expected)
        elif args.command == "recover":
            result = i.recover(args.mode)
        else:
            with offline(i.root, i.state):
                active = i.active()
                result = {
                    "active": active,
                    "recoveryRequired": (i.state / MARKER).exists(),
                    "app": str(i.version(active["bundleSha256"])) if active else None,
                }
        print(json.dumps(result))
        return 0
    except Exception as error:
        # No paths, credentials or exception text escape the operator boundary.
        from ptcg_online_client.contracts import ClientError

        code = (
            str(error)
            if isinstance(error, InstallError)
            else error.code
            if isinstance(error, ClientError)
            else "INSTALL_FAILED"
        )
        print(json.dumps({"status": "failed", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
