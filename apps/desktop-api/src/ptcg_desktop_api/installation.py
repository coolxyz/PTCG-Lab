"""Check the native profile before starting the product database.

The macOS installer pairs the profile with a particular bundle. Portable
Windows builds share the same private profile while its format is compatible.
"""

from contextlib import closing
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from .sealed_file import access
from .platform_files import private_directory
from ptcg_online_client.contracts import ClientError

RECORD = ".desktop-deployment.json"

MARKER = ".desktop-maintenance.json"

CONTRACT = {"schema": 1, "profileSchema": 1, "rulePolicy": "current-only-v1"}

SKIP = {"shell.lock", "native.lock", "focus.sock", "launch", MARKER}


class InstallError(ValueError):
    pass


def fail(code):
    raise InstallError(code)


def regular(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        fail("INSTALL_UNSAFE_PATH")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail("INSTALL_UNSAFE_FILE")
    return path


def digest(path):
    with regular(path).open("rb") as f:
        return file_digest(f, "sha256").hexdigest()


def read(path):
    raw = access(path, limit=16 * 1024 * 1024)

    def pairs(rows):
        result = {}
        for k, v in rows:
            if k in result:
                fail("INSTALL_INVALID_RECORD")
            result[k] = v
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def payload_record(payload):
    contract = json.loads(regular(Path(payload) / "INSTALLATION.json").read_bytes())
    if contract != CONTRACT:
        fail("DESKTOP_SCHEMA_UNSUPPORTED")
    return {**CONTRACT, "payloadSha256": digest(Path(payload) / "manifest.json")}


def browser_cache_entries(state, *, platform=None):
    """WebView2 initializes its private cache before the Python startup guard.

    Only that known runtime directory may precede a fresh Windows profile. This
    does not adopt existing product databases or credentials. The explicit
    platform argument is for contract tests, never an HTTP/operator setting.
    """
    if (sys.platform if platform is None else platform) != "win32":
        return set()
    path = Path(state) / "webview"
    if path.is_symlink() or path.is_junction():
        fail("INSTALL_UNSAFE_PATH")
    if path.exists():
        try:
            private_directory(path)
        except (OSError, ValueError, ClientError):
            fail("INSTALL_UNSAFE_PATH")
    return {"webview"}


def windows_profile_database(state):
    database = state / "app.sqlite"
    sidecars = [
        database.with_name(database.name + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    ]
    if not os.path.lexists(database):
        if any(os.path.lexists(sidecar) for sidecar in sidecars):
            fail("DESKTOP_INSTALL_REQUIRED")
        return False
    regular(database)
    for sidecar in sidecars:
        if os.path.lexists(sidecar):
            regular(sidecar)
    try:
        # Allow SQLite to recover its own journal under native.lock. mode=rw
        # ensures a failed check can never create an empty replacement database.
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != CONTRACT["profileSchema"]:
                fail("DESKTOP_SCHEMA_UNSUPPORTED")
            tables = {
                name
                for (name,) in db.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
    except sqlite3.DatabaseError:
        fail("DESKTOP_INSTALL_REQUIRED")
    if not {"collection", "decks", "revisions"} <= tables:
        fail("DESKTOP_INSTALL_REQUIRED")
    return True


def startup_guard(state, payload, *, platform=None):
    """Called under native.lock, before creating any application database."""
    state = Path(state)
    marker, record = state / MARKER, state / RECORD
    if os.path.lexists(marker):
        fail("DESKTOP_RECOVERY_REQUIRED")
    expected = payload_record(payload)
    windows = (sys.platform if platform is None else platform) == "win32"
    if os.path.lexists(record):
        saved = read(record)
        if (
            not isinstance(saved, dict)
            or saved.get("profileSchema") != CONTRACT["profileSchema"]
        ):
            fail("DESKTOP_SCHEMA_UNSUPPORTED")
        if windows:
            # Older releases recorded the payload hash at first launch. The
            # shell verifies the current payload separately before startup.
            previous_hash = saved.get("payloadSha256")
            if (
                set(saved) != set(expected)
                or any(
                    type(saved[key]) is not type(value) or saved[key] != value
                    for key, value in CONTRACT.items()
                )
                or not isinstance(previous_hash, str)
                or not re.fullmatch(r"[a-f0-9]{64}", previous_hash)
            ):
                fail("DESKTOP_INSTALL_REQUIRED")
            windows_profile_database(state)
        elif saved != expected:
            fail("DESKTOP_INSTALL_REQUIRED")
        return
    allowed = (
        SKIP
        | {"connection.json", ".ptcg-acceptance.json"}
        | browser_cache_entries(state, platform=platform)
    )
    if any(p.name not in allowed for p in state.iterdir()):
        # A pre-record Windows profile can be recognized by its existing
        # product database. Leave it in place, including DPAPI path bindings.
        if not windows or not windows_profile_database(state):
            fail("DESKTOP_INSTALL_REQUIRED")
        return
    access(record, data=json.dumps(expected, sort_keys=True).encode())
