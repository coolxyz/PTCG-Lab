"""Native startup guard only; product backup/migration is an operator tool.

No SQLite, product imports, installation endpoint or update authority lives in
this bridge package. The payload manifest binds one currently installed build.
"""

from hashlib import file_digest
import json
from pathlib import Path
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


def startup_guard(state, payload):
    """Called under native.lock, before creating any application database."""
    state = Path(state)
    marker, record = state / MARKER, state / RECORD
    if marker.exists() or marker.is_symlink():
        fail("DESKTOP_RECOVERY_REQUIRED")
    expected = payload_record(payload)
    if record.exists() or record.is_symlink():
        saved = read(record)
        if not isinstance(saved, dict) or saved.get("profileSchema") != 1:
            fail("DESKTOP_SCHEMA_UNSUPPORTED")
        if saved != expected:
            fail("DESKTOP_INSTALL_REQUIRED")
    else:
        # Empty first launch is safe. Existing C2/user data needs explicit
        # adoption with a backup; never upgrade it merely by double-clicking.
        allowed = (
            SKIP
            | {"connection.json", ".ptcg-acceptance.json"}
            | browser_cache_entries(state)
        )
        if any(p.name not in allowed for p in state.iterdir()):
            fail("DESKTOP_INSTALL_REQUIRED")
        access(record, data=json.dumps(expected, sort_keys=True).encode())
