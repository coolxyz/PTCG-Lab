"""Explicit platform security checks; POSIX mode bits never stand in for NTFS ACLs."""

import os
from pathlib import Path
import sys


def private_directory(path):
    path = Path(path)
    if sys.platform == "win32":
        from .windows_storage import WindowsDirectory

        owner = WindowsDirectory(path)
        owner.close()
    else:
        if path.is_symlink():
            raise ValueError("Unsafe private directory")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.stat()
        if info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("Unsafe private directory permissions")
    return path
