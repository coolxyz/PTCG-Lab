"""Read checked-out images only: no Git process, checkout, config or network."""

import os
from pathlib import Path
import stat

from .common import SyncError
from .http_source import HTTPSource, object_sha
from .raster import raster

MAX_IMAGE = 8 * 1024**2


def local_path(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(c in value for c in ("\0", "\n", "\r")):
        raise SyncError("INVALID_LOCAL_IMAGE_SOURCE", "请填写本地 Git 仓库的绝对路径")
    if value.startswith(("//", "\\\\")):
        raise SyncError("INVALID_LOCAL_IMAGE_SOURCE", "请选择这台电脑上的本地目录")
    try:
        root = Path(value).expanduser()
    except (ValueError, RuntimeError, OSError) as error:
        raise SyncError("INVALID_LOCAL_IMAGE_SOURCE", "本地目录路径无效") from error
    if not root.is_absolute() or ".." in root.parts:
        raise SyncError("INVALID_LOCAL_IMAGE_SOURCE", "请选择仓库根目录的绝对路径，不要使用 ..")
    return root


class LocalGitArtwork:
    def __init__(self, directory):
        self.root = local_path(directory)

    @staticmethod
    def checked(path):
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise SyncError("UNSAFE_LOCAL_IMAGE_SOURCE", "本地卡图路径不能包含符号链接或目录联接")
        return path

    def validate(self):
        self.checked(self.root)
        marker = self.checked(self.root / ".git")
        images = self.checked(self.root / "img")
        if not self.root.is_dir() or not (marker.is_dir() or marker.is_file()) or not images.is_dir():
            raise SyncError("INVALID_LOCAL_IMAGE_SOURCE", "请选择包含 .git 和 img 的克隆仓库根目录")
        return self.root

    def read(self, relative, blob):
        HTTPSource.validate_path(relative)
        HTTPSource.validate_sha(blob)
        if not relative.startswith("img/") or Path(relative).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise SyncError("INVALID_LOCAL_IMAGE_PATH", "只读取索引指定的 img 目录内卡图")
        path = self.checked(self.validate() / relative)
        try:
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= MAX_IMAGE:
                raise SyncError("INVALID_LOCAL_IMAGE", "本地卡图类型或大小无效（单图最多 8 MiB）")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= MAX_IMAGE:
                    raise SyncError("INVALID_LOCAL_IMAGE", "本地卡图类型或大小无效（单图最多 8 MiB）")
                data = stream.read(MAX_IMAGE + 1)
        except FileNotFoundError as error:
            raise SyncError("LOCAL_IMAGE_MISSING", "克隆目录缺少这张卡图，请完整下载原图", 404) from error
        except OSError as error:
            raise SyncError("LOCAL_IMAGE_UNREADABLE", "本地卡图不可读取，请检查目录和访问权限") from error
        if len(data) > MAX_IMAGE or object_sha("blob", data) != blob:
            raise SyncError("LOCAL_IMAGE_HASH_MISMATCH", "本地卡图与 App 固定版本不一致；请检查原图或 Git LFS 文件")
        return data, raster(data)
