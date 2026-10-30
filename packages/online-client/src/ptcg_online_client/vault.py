"""Single native owner, atomic private credential/journal storage with explicit platform file adapters."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
from threading import RLock
import uuid
from copy import deepcopy
from .contracts import ClientError, unpack

MAX_VAULT = 8 * 1024 * 1024


class PosixFiles:
    """Default browser/POSIX backend; never silently selected on Windows."""

    def __init__(self, root, key):
        if os.name != "posix":
            raise ClientError("CREDENTIAL_BACKEND_UNAVAILABLE")
        import fcntl

        self.directory = Path(root)
        if self.directory.is_symlink():
            raise ClientError("UNSAFE_STORAGE")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if (
            self.directory.stat().st_mode & 0o077
            or self.directory.stat().st_uid != os.getuid()
        ):
            raise ClientError("UNSAFE_STORAGE")
        self.directory = self.directory.resolve()
        self.path = self.directory / (key + ".json")
        self.lock = os.open(
            self.directory / (key + ".lock"),
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(self.lock)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ClientError("UNSAFE_STORAGE")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ClientError:
            os.close(self.lock)
            raise
        except OSError:
            os.close(self.lock)
            raise ClientError("PROFILE_IN_USE") from None

    def read(self, limit):
        if not (self.path.exists() or self.path.is_symlink()):
            return None
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > limit
            ):
                raise ClientError("UNSAFE_STORAGE")
            return source.read(limit + 1)

    def replace(self, data):
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
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None


class Vault:
    def __init__(
        self,
        root,
        server,
        *,
        codec_factory=None,
        initial_state=None,
        storage_factory=PosixFiles,
        max_encoded_bytes=MAX_VAULT + 64,
        require_existing=False,
        require_new=False,
    ):
        self.mutex, self.closed, self.failed = RLock(), False, False
        self.codec = None
        self.storage = storage_factory(root, server.key)
        self.directory, self.path = self.storage.directory, self.storage.path
        self.lock = self.storage.lock
        self.max_encoded_bytes = max_encoded_bytes if codec_factory else MAX_VAULT
        try:
            raw = self.storage.read(self.max_encoded_bytes)
            if raw is None and require_existing:
                raise ClientError("IDENTITY_SOURCE_MISSING")
            if raw is not None and require_new:
                raise ClientError("IDENTITY_DESTINATION_EXISTS")
            if raw is not None:
                if len(raw) > self.max_encoded_bytes:
                    raise ClientError("UNSAFE_STORAGE")
                if codec_factory:
                    self.codec = codec_factory(create=False, existing=raw)
                    raw = self.codec.decode(raw)
                if len(raw) > MAX_VAULT:
                    raise ClientError("INVALID_STORAGE")
                self.state = unpack(raw)
            else:
                self.state = (
                    deepcopy(initial_state)
                    if initial_state is not None
                    else {
                        "schema": 1,
                        "profile": server.key,
                        "credentials": None,
                        "refreshPending": False,
                        "pending": {},
                        "controllers": {},
                        "snapshots": {},
                    }
                )
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
            if raw is None:
                if codec_factory:
                    self.codec = codec_factory(create=True, existing=None)
                self.save()
        except BaseException:
            self.storage.close()
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
        if self.codec:
            try:
                data = self.codec.encode(data)
            except Exception:
                self.failed = True
                raise ClientError("STORAGE_UNAVAILABLE") from None
        try:
            if len(data) > self.max_encoded_bytes:
                raise ClientError("STORAGE_CAPACITY")
            self.storage.replace(data)
        except (OSError, ClientError):
            self.failed = True
            raise ClientError("STORAGE_UNAVAILABLE") from None

    def close(self):
        with self.mutex:
            if not self.closed:
                self.closed = True
                self.storage.close()
