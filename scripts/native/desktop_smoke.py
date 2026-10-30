"""Test an actual packaged GUI using fresh offline profiles and private pipes.

Only owned Popen processes are terminated. No existing profile or LAN is selected.
Mac self-verification is recorded as Mac, never native Windows acceptance.
"""

import argparse
import json
from hashlib import sha256
import sys
import os
from pathlib import Path
import platform
import queue
import secrets
import socket
import subprocess
import tempfile
from threading import Thread
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.packaging.source_identity import source_digest  # noqa: E402

CHECKS = {
    "appRendered",
    "sameOriginSession",
    "offlineBridge",
    "cardCatalog",
    "deckPersistence",
}


def ready(value, nonce):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "type", "nonce", "backendPid", "origin", "checks"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["type"] != "gui-ready"
        or value["nonce"] != nonce
    ):
        raise ValueError("Invalid GUI frame")
    u = urlsplit(value["origin"])
    if (
        u.scheme != "http"
        or u.hostname != "127.0.0.1"
        or not u.port
        or value["origin"] != f"http://127.0.0.1:{u.port}"
        or type(value["backendPid"]) is not int
        or value["backendPid"] <= 0
    ):
        raise ValueError("GUI origin/PID not owned loopback")
    if (
        not isinstance(value["checks"], dict)
        or set(value["checks"]) != CHECKS
        or not all(v is True for v in value["checks"].values())
    ):
        raise ValueError("GUI checks incomplete")
    return u.port


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise RuntimeError("Cannot observe owned backend")
        try:
            status = k.WaitForSingleObject(handle, 0)
            if status not in (0, 258):
                raise RuntimeError("Cannot observe owned backend exit")
            return status == 258
        finally:
            k.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def check_closed(pid, port):
    until = time.monotonic() + 10
    while alive(pid) and time.monotonic() < until:
        time.sleep(0.05)
    if alive(pid):
        raise RuntimeError("Owned backend survived GUI exit")
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError("Owned listener survived GUI exit")


class Child:
    def __init__(self, exe, profile, nonce, mode, log):
        self.nonce = nonce
        self.log = log.open("xb")
        try:
            self.process = subprocess.Popen(
                [str(exe), "--acceptance"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                text=True,
                encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except BaseException:
            self.log.close()
            raise
        try:
            self.process.stdin.write(
                json.dumps(
                    {
                        "schema": 1,
                        "nonce": nonce,
                        "stateDir": str(profile),
                        "mode": mode,
                    }
                )
                + "\n"
            )
            self.process.stdin.flush()
        except BaseException:
            self.close()
            raise
        self.replies = queue.Queue(maxsize=1)

        def read():
            try:
                self.replies.put(self.process.stdout.readline(8193))
            except Exception:
                self.replies.put("")

        Thread(target=read, daemon=True).start()

    def frame(self):
        try:
            line = self.replies.get(timeout=60)
        except queue.Empty:
            raise RuntimeError("Native GUI readiness timeout") from None
        if len(line) > 8192 or not line.endswith("\n"):
            raise ValueError("Missing/bounded GUI frame")
        value = json.loads(line)
        if value.get("nonce") != self.nonce:
            raise ValueError("Wrong GUI run")
        return value

    def stop(self, *, eof=False):
        if self.process.poll() is not None:
            raise RuntimeError("GUI exited before controlled shutdown")
        if self.process.poll() is None:
            if not eof:
                self.process.stdin.write(
                    json.dumps({"schema": 1, "type": "shutdown", "nonce": self.nonce})
                    + "\n"
                )
                self.process.stdin.flush()
            self.process.stdin.close()
            code = self.process.wait(timeout=15)
            if code != (1 if eof else 0):
                raise RuntimeError("Unexpected GUI exit status")

    def close(self):
        if self.process.poll() is None:
            try:
                self.stop(eof=True)
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=10)
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def smoke(app, output):
    app = Path(app).absolute()
    output = Path(output).absolute()
    if os.name == "nt":
        if app.suffix.lower() != ".exe" or platform.machine().lower() not in {
            "amd64",
            "x86_64",
        }:
            raise ValueError("Windows x64 EXE required")
        exe = app
        target = "windows-x64"
    elif platform.system() == "Darwin":
        exe = app / "Contents/MacOS/ptcg-native-shell"
        target = "macos-arm64"
    else:
        raise ValueError("Use native Windows or Mac")
    if (
        not exe.is_file()
        or output.exists()
        or any(p.is_symlink() for p in (output, *output.parents))
    ):
        raise ValueError("Choose an existing packaged app and a NEW result directory")
    output.mkdir(parents=True)
    children = []
    nonce = secrets.token_hex(32)
    # Short canonical Mac paths satisfy the Unix focus socket limit. Windows
    # exercises Unicode/spaces; long-path DPAPI is tested separately in pytest.
    with tempfile.TemporaryDirectory(
        prefix="ptcg-b4-", dir="/private/tmp" if os.name != "nt" else None
    ) as temp:
        profile = Path(temp).resolve() / ("中文 profile" if os.name == "nt" else "p")
        try:
            first = Child(exe, profile, nonce, "new", output / "first.stderr.log")
            children.append(first)
            f = first.frame()
            (output / "first.checks.json").write_text(
                json.dumps(
                    {
                        "type": f.get("type")
                        if f.get("type") in {"gui-ready", "gui-failed", "error"}
                        else "invalid",
                        "checks": {
                            k: v
                            for k, v in f.get("checks", {}).items()
                            if k in CHECKS and type(v) is bool
                        },
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            port = ready(f, nonce)
            if not alive(f["backendPid"]):
                raise RuntimeError("GUI backend exited early")
            second = Child(exe, profile, nonce, "reopen", output / "focus.stderr.log")
            children.append(second)
            if second.frame() != {"schema": 1, "type": "focused", "nonce": nonce}:
                raise ValueError("Second instance created a new GUI")
            if second.process.wait(timeout=10) != 0:
                raise RuntimeError("Focus process failed")
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
            first.stop()
            check_closed(f["backendPid"], port)
            reopened = Child(
                exe, profile, nonce, "reopen", output / "reopen.stderr.log"
            )
            children.append(reopened)
            r = reopened.frame()
            p = ready(r, nonce)
            if not alive(r["backendPid"]):
                raise RuntimeError("Reopened backend exited")
            reopened.stop(eof=True)
            check_closed(r["backendPid"], p)
            report = {
                "schema": 1,
                "stage": "P8-B-desktop-smoke",
                "status": "passed",
                "platform": target,
                "sourceSha256": source_digest(ROOT),
                "executableSha256": sha256(exe.read_bytes()).hexdigest(),
                "checks": {
                    "actualWebview": True,
                    "sameOriginAuthorization": True,
                    "offlineProfile": True,
                    "singleInstanceFocus": True,
                    "normalExit": True,
                    "sameProfileReopen": True,
                    "deckPersistence": True,
                    "parentPipeEof": True,
                    "ownedBackendCleanup": True,
                },
                "manualVisualAccepted": False,
                "networkMatchAccepted": False,
                "releaseReady": False,
            }
            (output / "report.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
            return report
        finally:
            for child in reversed(children):
                child.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(smoke(a.app, a.output)))
