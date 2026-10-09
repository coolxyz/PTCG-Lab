"""Fetch an immutable commit from the configured repository, with no checkout."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

from .common import REPOSITORY, SyncError, read, write


class GitSource:
    def __init__(self, directory, repository=REPOSITORY, *, allow_local=False):
        if not (allow_local and Path(repository).is_dir()):
            from .repositories import canonical
            repository = canonical(repository)
        self.directory = Path(directory)
        self.repository = repository
        self.allow_local = allow_local

    def git(self, *args, timeout=180):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        # Portable Git distributions may keep HTTPS helpers beside mingw64/bin
        # without placing that directory on the inherited application PATH.
        executable = shutil.which("git")
        if executable and os.name == "nt":
            helper_bin = Path(executable).parent.parent / "mingw64" / "bin"
            if (helper_bin / "git-remote-https.exe").is_file():
                env["PATH"] = str(helper_bin) + os.pathsep + env.get("PATH", "")
        try:
            r = subprocess.run(
                ["git", "-c", "core.hooksPath=", "-c", "credential.interactive=false", "-c", "http.version=HTTP/1.1", "-c", "http.lowSpeedLimit=1", "-c", "http.lowSpeedTime=60", *args],
                cwd=self.directory if self.directory.exists() else None,
                env=env, capture_output=True, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SyncError("SOURCE_UNAVAILABLE", "上游获取失败或超时，可重试", 503) from e
        if r.returncode:
            # Do not expose credential-bearing diagnostics to the browser.
            raise SyncError("GIT_FAILED", "Git 操作未完成，请检查网络和上游缓存", 503)
        return r.stdout

    def cache_all(self, sha):
        """Materialize every file at a pinned revision without a mutable checkout."""
        self.validate_sha(sha)
        self.initialize()
        def inspect():
            return self.git("rev-list", "--objects", "--missing=print", sha + "^{tree}").decode().splitlines()
        try:
            objects = inspect()
        except SyncError:
            objects = None
        if objects is None or any(line.startswith("?") for line in objects):
            self.git("fetch", "--refetch", "--no-filter", "--no-tags", "origin", sha, timeout=3600)
            objects = inspect()
        missing = sum(line.startswith("?") for line in objects)
        if missing:
            raise SyncError("CACHE_INCOMPLETE", "固定版本仍有未下载对象，请重试", 503)
        return {"commit": sha, "directory": str(self.directory.resolve()), "objects": len(objects), "missing": 0, "files": len(self.tree(sha)), "mode": "local-git-objects"}

    def initialize(self):
        if not (self.directory / ".git").exists():
            if self.directory.exists() and any(self.directory.iterdir()):
                raise SyncError("CACHE_NOT_EMPTY", "上游缓存目录非空，未执行覆盖")
            self.directory.mkdir(parents=True, exist_ok=True)
            self.git("init")
            self.git("remote", "add", "origin", self.repository)
        remote = self.git("remote", "get-url", "origin").decode().strip().rstrip("/").removesuffix(".git")
        if remote != self.repository.rstrip("/").removesuffix(".git"):
            raise SyncError("REMOTE_MISMATCH", "缓存 origin 与配置不一致，未修改缓存")
        # No-checkout clones contain staged deletions; inspect physical tracked files
        # only after an index exists. Our normal cache never writes an index.
        if (self.directory / ".git/index").exists():
            if self.git("status", "--porcelain").strip():
                raise SyncError("DIRTY_UPSTREAM", "上游工作区有修改，请保留后另行处理")

    def fetch(self):
        self.initialize()
        args = ["fetch", "--no-tags"]
        if not self.allow_local:
            args += ["--filter=blob:none"]
        self.git(*args, "origin", "+refs/heads/main:refs/remotes/origin/main")
        sha = self.git("rev-parse", "refs/remotes/origin/main^{commit}").decode().strip()
        self.validate_sha(sha)
        return sha

    @staticmethod
    def validate_sha(sha):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise SyncError("INVALID_COMMIT", "必须使用完整的固定提交 SHA")

    @staticmethod
    def validate_path(path):
        if not isinstance(path, str) or "\\" in path or ":" in path or "\x00" in path:
            raise SyncError("INVALID_ASSET_PATH", "上游文件路径无效")
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise SyncError("INVALID_ASSET_PATH", "上游文件路径无效")

    def blob(self, sha, path):
        self.validate_sha(sha)
        self.validate_path(path)
        entry = self.git("ls-tree", sha, "--", path).decode().strip()
        if not entry or entry.split()[0] not in ("100644", "100755"):
            raise SyncError("ASSET_NOT_FILE", "上游引用不是普通文件")
        return self.git("show", f"{sha}:{path}")

    def tree(self, sha):
        self.validate_sha(sha)
        result = {}
        for entry in self.git("ls-tree", "-r", "-z", sha).split(b"\x00"):
            if entry:
                meta, path = entry.decode().split("\t", 1)
                mode, kind, object_id = meta.split()
                if mode in ("100644", "100755") and kind == "blob":
                    result[path] = object_id
        return result

    def snapshot(self, sha, target):
        import json
        raw = self.blob(sha, "ptcg_chs_infos.json")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as e:
            raise SyncError("INVALID_JSON", "上游 JSON 无法解析") from e
        write(Path(target) / "upstream.json", value)
        tree = self.tree(sha)
        write(Path(target) / "source.json", {"repository": self.repository, "commit": sha, "tree": tree})
        return value, tree

    def is_ancestor(self, previous, current):
        if previous == current:
            return True
        try:
            self.git("merge-base", "--is-ancestor", previous, current)
            return True
        except SyncError:
            return False
