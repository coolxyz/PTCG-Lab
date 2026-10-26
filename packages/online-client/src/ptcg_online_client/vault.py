"""Single native owner, atomic private credential/journal storage on POSIX."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from threading import RLock
import uuid
from .contracts import ClientError, unpack

MAX_VAULT = 8 * 1024 * 1024


class Vault:
    def __init__(self, root, server):
        if os.name != "posix":
            raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
        import fcntl

        self.mutex, self.closed, self.failed = RLock(), False, False
        self.directory = Path(root)
        if self.directory.is_symlink():
            raise ClientError("UNSAFE_STORAGE")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.directory.stat().st_mode) & 0o077:
            raise ClientError("UNSAFE_STORAGE")
        self.directory = self.directory.resolve()
        self.path = self.directory / (server.key + ".json")
        self.lock = os.open(
            self.directory / (server.key + ".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.lock)
            raise ClientError("PROFILE_IN_USE") from None
        try:
            if self.path.exists() or self.path.is_symlink():
                fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) & 0o077
                        or info.st_size > MAX_VAULT
                    ):
                        raise ClientError("UNSAFE_STORAGE")
                    self.state = unpack(source.read(MAX_VAULT + 1))
                if (
                    not isinstance(self.state, dict)
                    or self.state.get("schema") != 1
                    or self.state.get("profile") != server.key
                    or type(self.state.get("refreshPending")) is not bool
                    or "credentials" not in self.state
                    or not isinstance(self.state["credentials"], (dict, type(None)))
                    or any(
                        not isinstance(self.state.get(k), dict)
                        for k in ("pending", "controllers", "snapshots")
                    )
                    or not isinstance(self.state.get("controlPending", {}), dict)
                    or not isinstance(self.state.get("matchHistory", {}), dict)
                ):
                    raise ClientError("INVALID_STORAGE")
            else:
                self.state = {
                    "schema": 1,
                    "profile": server.key,
                    "credentials": None,
                    "refreshPending": False,
                    "pending": {},
                    "controllers": {},
                    "snapshots": {},
                }
                self.save()
        except Exception:
            os.close(self.lock)
            raise

    @contextmanager
    def transaction(self):
        with self.mutex:
            if self.closed:
                raise ClientError("CLIENT_CLOSED")
            if self.failed:
                raise ClientError("STORAGE_UNAVAILABLE")
            yield self.state

    def save(self):
        data = json.dumps(self.state, allow_nan=False, separators=(",", ":")).encode()
        if len(data) > MAX_VAULT:
            self.failed = True
            raise ClientError("STORAGE_CAPACITY")
        temp = self.directory / ("." + uuid.uuid4().hex)
        try:
            fd = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # A failed durable write must not leave a usable, memory-only journal.
            self.failed = True
            raise ClientError("STORAGE_UNAVAILABLE") from None
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                # Preserve the original, redacted storage error.
                pass

    def close(self):
        with self.mutex:
            if not self.closed:
                self.closed = True
                os.close(self.lock)
