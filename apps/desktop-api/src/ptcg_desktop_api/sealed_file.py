"""Private operator files, no overwrite or permission repair. Never used by HTTP."""

import os
from pathlib import Path
import stat
import sys

from ptcg_online_client.contracts import ClientError
from .platform_files import private_directory


def read_encrypted(path, *, limit):
    """An operator may import ciphertext copied to ordinary local media.

    Authentication happens before any identity is persisted. This reader grants
    no confidentiality claim to the input ACL and never returns plaintext.
    """
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ClientError("UNSAFE_STORAGE")
    if sys.platform == "win32":
        import win32con as c
        import win32file as f
        import pywintypes
        from .windows_storage import WindowsDirectory, FILE_FLAG_OPEN_REPARSE_POINT

        if str(path).startswith("\\\\") or any(
            ":" in p or p.rstrip(". ") != p for p in path.parts[1:]
        ):
            raise ClientError("UNSAFE_STORAGE")
        handle = None
        try:
            handle = f.CreateFile(
                WindowsDirectory.full(path),
                c.GENERIC_READ,
                c.FILE_SHARE_READ,
                None,
                c.OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            info = f.GetFileInformationByHandle(handle)
            size = (info[5] << 32) | info[6]
            if (
                info[0] & (c.FILE_ATTRIBUTE_REPARSE_POINT | c.FILE_ATTRIBUTE_DIRECTORY)
                or info[7] != 1
                or size > limit
            ):
                raise ClientError("UNSAFE_STORAGE")
            result = bytearray()
            while len(result) < size:
                _, chunk = f.ReadFile(handle, min(65536, size - len(result)))
                if not chunk:
                    raise ClientError("UNSAFE_STORAGE")
                result.extend(chunk)
            return bytes(result)
        except pywintypes.error:
            raise ClientError("PRIVATE_FILE_UNAVAILABLE") from None
        finally:
            if handle is not None:
                handle.Close()
    if os.name != "posix":
        raise ClientError("UNSAFE_STORAGE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > limit
            ):
                raise ClientError("UNSAFE_STORAGE")
            result = stream.read(limit + 1)
            if len(result) > limit:
                raise ClientError("UNSAFE_STORAGE")
            return result
    except OSError:
        raise ClientError("PRIVATE_FILE_UNAVAILABLE") from None


def access(path, *, data=None, limit=9 * 1024 * 1024):
    """Read or exclusively create a bounded binary file under a private parent."""
    path = Path(path).absolute()
    if not path.name or len(data or b"") > limit:
        raise ClientError("UNSAFE_STORAGE")
    if sys.platform == "win32":
        from .windows_storage import WindowsDirectory

        if ":" in path.name or path.name.rstrip(". ") != path.name:
            raise ClientError("UNSAFE_STORAGE")
        owner = WindowsDirectory(path.parent)
        handle = None
        try:
            c = owner.con
            handle = owner.open_file(
                path,
                c.GENERIC_READ if data is None else c.GENERIC_WRITE,
                c.OPEN_EXISTING if data is None else c.CREATE_NEW,
            )
            size = owner.inspect(handle)
            if size > limit:
                raise ClientError("UNSAFE_STORAGE")
            if data is None:
                raw = bytearray()
                while len(raw) < size:
                    _, chunk = owner.file.ReadFile(handle, min(65536, size - len(raw)))
                    if not chunk:
                        raise ClientError("UNSAFE_STORAGE")
                    raw.extend(chunk)
                return bytes(raw)
            offset = 0
            while offset < len(data):
                _, count = owner.file.WriteFile(handle, data[offset : offset + 65536])
                if count <= 0:
                    raise ClientError("STORAGE_UNAVAILABLE")
                offset += count
            owner.file.FlushFileBuffers(handle)
        except owner.error:
            raise ClientError("PRIVATE_FILE_UNAVAILABLE") from None
        finally:
            if handle is not None:
                handle.Close()
            owner.close()
        return
    if os.name != "posix" or any(
        p.is_symlink() for p in (path.parent, *path.parent.parents)
    ):
        raise ClientError("UNSAFE_STORAGE")
    private_directory(path.parent)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        flags = os.O_RDONLY if data is None else os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(
            path.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory
        )
        with os.fdopen(fd, "rb" if data is None else "wb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > limit
            ):
                raise ClientError("UNSAFE_STORAGE")
            if data is None:
                result = stream.read(limit + 1)
                if len(result) > limit:
                    raise ClientError("UNSAFE_STORAGE")
                return result
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    except OSError:
        raise ClientError("PRIVATE_FILE_UNAVAILABLE") from None
    finally:
        os.close(directory)
