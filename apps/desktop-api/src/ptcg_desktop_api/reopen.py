"""Explicit native reopen IPC in the user's private state directory, never HTTP."""

import json
import fcntl
import os
from pathlib import Path
import socket
import stat
from threading import Event, Thread


def private_root(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Invalid native state")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Native state must be owned by this user and private")
    return path


class ReopenServer:
    def __init__(self, state, host, config_hash):
        self.path = private_root(state) / "reopen.sock"
        lock_path = self.path.with_name("product.lock")
        if lock_path.is_symlink():
            raise ValueError("Unsafe product lock")
        self.lock = lock_path.open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_path.chmod(0o600)
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ValueError("Unsafe native socket")
                # The exclusive product lock proves no cooperating owner is alive.
                self.path.unlink()
        except BaseException:
            self.lock.close()
            raise
        self.stop = Event()
        self.sock = socket.socket(socket.AF_UNIX)
        try:
            self.sock.bind(str(self.path))
        except BaseException:
            self.sock.close()
            self.lock.close()
            raise
        self.path.chmod(0o600)
        self.sock.listen(2)
        self.sock.settimeout(0.2)
        self.host, self.config_hash = host, config_hash
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                try:
                    raw = b""
                    while not raw.endswith(b"\n") and len(raw) < 256:
                        part = connection.recv(256)
                        if not part:
                            break
                        raw += part
                    request = json.loads(raw)
                    if request != {"config": self.config_hash}:
                        result = {"error": "CONFIG_MISMATCH"}
                    else:
                        result = {
                            "origin": self.host.origin,
                            "code": self.host.activation_code(),
                        }
                    connection.sendall(json.dumps(result).encode() + b"\n")
                except Exception:
                    # No native secret/error details go into logs.
                    pass

    def close(self):
        self.stop.set()
        self.sock.close()
        self.thread.join(timeout=3)
        self.path.unlink(missing_ok=True)
        self.lock.close()


def reopen(state, config_hash):
    path = private_root(state) / "reopen.sock"
    info = path.lstat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Unsafe native control socket")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(3)
        sock.connect(str(path))
        sock.sendall(json.dumps({"config": config_hash}).encode() + b"\n")
        raw = b""
        while not raw.endswith(b"\n") and len(raw) < 1024:
            part = sock.recv(1024)
            if not part:
                break
            raw += part
    result = json.loads(raw)
    if "error" in result:
        raise ValueError(result["error"])
    return result
