"""Local source settings and isolated caches; no credentials in repository URLs."""

import hashlib
from pathlib import Path
import re
import configparser

from .common import REPOSITORY, SyncError, read


def canonical(repository):
    if not isinstance(repository, str):
        raise SyncError("INVALID_REPOSITORY", "请输入 GitHub HTTPS 仓库地址")
    value = repository.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
        value,
    ):
        raise SyncError(
            "INVALID_REPOSITORY",
            "仅支持公开 GitHub 仓库的 HTTPS 地址，不接受凭据、参数或本地路径",
        )
    return value


def legacy_repository(root):
    config = Path(root) / ".catalog/upstream/PTCG-CHS-Datasets/.git/config"
    if config.is_file():
        parser = configparser.RawConfigParser()
        try:
            parser.read(config, encoding="utf-8")
            value = parser.get('remote "origin"', "url", fallback="")
        except (configparser.Error, UnicodeError, OSError):
            return None
        try:
            return canonical(value)
        except SyncError:
            pass
    return None


def configured(home, root):
    path = Path(home) / "repository.json"
    if path.exists():
        return canonical(read(path)["repository"])
    # Existing installations retain their selected fork; new ones use the original.
    return legacy_repository(root) or REPOSITORY


def source_for(root, repository):
    from .http_source import HTTPSource

    repository = canonical(repository)
    base = Path(root) / ".catalog/upstream"
    directory = base / ("repo-" + hashlib.sha256(repository.encode()).hexdigest())
    return HTTPSource(directory, repository)
