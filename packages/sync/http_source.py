"""Pinned public GitHub data via TLS and verified Git tree/blob objects; no Git CLI.

The commit -> root tree binding is obtained from GitHub's HTTPS Git API (not
an independently verified commit signature). Tree and blob bytes are hashed.
Content is data only; no source executable, hook or credential is accepted.
"""

from hashlib import sha1
import http.client
import json
from pathlib import Path, PurePosixPath
import re
import ssl
import time
from threading import RLock

import certifi
from .common import SyncError, read, write

MAX_FILE = 96 * 1024 * 1024
MAX_TREE = 24 * 1024 * 1024


def object_sha(kind, data):
    return sha1(
        kind.encode() + b" " + str(len(data)).encode() + b"\0" + data
    ).hexdigest()


def parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError, TypeError) as e:
        raise SyncError("INVALID_SOURCE_JSON", "上游响应格式无效", 503) from e


class HTTPSource:
    def __init__(self, directory, repository, *, transport=None):
        from .repositories import canonical

        self.repository = canonical(repository)
        self.directory = Path(directory)
        self.repo = self.repository.removeprefix("https://github.com/")
        self.transport = transport or self._https
        self.lock = RLock()
        self._trees = {}

    @staticmethod
    def validate_sha(sha):
        if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{40}", sha):
            raise SyncError("INVALID_COMMIT", "必须使用完整的固定提交 SHA")

    @staticmethod
    def validate_path(path):
        if (
            not isinstance(path, str)
            or not path
            or len(path.encode()) > 2048
            or any(c in path for c in ("\\", ":", "\0"))
        ):
            raise SyncError("INVALID_ASSET_PATH", "上游文件路径无效")
        value = PurePosixPath(path)
        if (
            value.is_absolute()
            or value.as_posix() != path
            or any(p in {".", ".."} for p in value.parts)
        ):
            raise SyncError("INVALID_ASSET_PATH", "上游文件路径无效")

    @staticmethod
    def _https(path, limit, *, raw=False):
        connection = http.client.HTTPSConnection(
            "api.github.com",
            timeout=25,
            context=ssl.create_default_context(cafile=certifi.where()),
        )
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Accept": "application/vnd.github.raw+json"
                    if raw
                    else "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "PTCG-Lab-data-source",
                    "Accept-Encoding": "identity",
                },
            )
            reply = connection.getresponse()
            if reply.status in (403, 429):
                raise SyncError(
                    "SOURCE_RATE_LIMIT",
                    "上游访问额度暂不可用，请稍后重试；固定版本和已有资源保留",
                    503,
                )
            if reply.status != 200:
                raise SyncError(
                    "SOURCE_UNAVAILABLE",
                    "上游数据获取失败；不跟随重定向，请稍后重试",
                    503,
                )
            length = reply.getheader("Content-Length")
            if length and (not length.isdecimal() or int(length) > limit):
                raise SyncError("SOURCE_TOO_LARGE", "上游文件超过下载预算", 503)
            chunks = []
            total = 0
            deadline = time.monotonic() + 180
            while block := reply.read(min(64 * 1024, limit + 1 - total)):
                if time.monotonic() > deadline:
                    raise SyncError(
                        "SOURCE_UNAVAILABLE", "上游下载超时，固定版本保留", 503
                    )
                chunks.append(block)
                total += len(block)
                if total > limit:
                    raise SyncError("SOURCE_TOO_LARGE", "上游文件超过下载预算", 503)
            return b"".join(chunks)
        except SyncError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise SyncError(
                "SOURCE_UNAVAILABLE", "上游网络连接失败或超时，可重试", 503
            ) from e
        finally:
            connection.close()

    def _safe(self, name):
        self.validate_path(name)
        path = self.directory / name
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise SyncError("UNSAFE_SOURCE_CACHE", "上游缓存包含链接，未写入")
        return path

    def initialize(self):
        path = self._safe("origin.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if read(path) != {"schema": 1, "repository": self.repository}:
                raise SyncError("REMOTE_MISMATCH", "缓存来源不一致，未覆盖")
        else:
            write(path, {"schema": 1, "repository": self.repository})

    def api(self, suffix, limit=MAX_TREE, *, raw=False):
        for attempt in range(3):
            try:
                result = self.transport(
                    "/repos/" + self.repo + "/" + suffix, limit, raw=raw
                )
                break
            except SyncError as error:
                if error.code != "SOURCE_UNAVAILABLE" or attempt == 2:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if not isinstance(result, bytes) or len(result) > limit:
            raise SyncError("SOURCE_TOO_LARGE", "上游响应超过预算", 503)
        return result if raw else parse(result)

    def fetch(self):
        self.initialize()
        value = self.api("commits/main", 1024 * 1024)
        sha = value.get("sha")
        self.validate_sha(sha)
        return sha

    def commit(self, sha):
        self.validate_sha(sha)
        self.initialize()
        path = self._safe("commits/" + sha + ".json")
        if path.exists():
            value = parse(path.read_bytes())
        else:
            raw = self.api("git/commits/" + sha, 1024 * 1024)
            try:
                value = {
                    "sha": raw["sha"],
                    "tree": raw["tree"]["sha"],
                    "parents": [p["sha"] for p in raw["parents"]],
                }
            except (KeyError, TypeError) as e:
                raise SyncError("INVALID_COMMIT", "上游提交结构无效", 503) from e
        if (
            value.get("sha") != sha
            or not isinstance(value.get("parents"), list)
            or len(value["parents"]) > 32
        ):
            raise SyncError("INVALID_COMMIT", "上游提交身份无效", 503)
        self.validate_sha(value.get("tree"))
        for parent in value["parents"]:
            self.validate_sha(parent)
        if not path.exists():
            write(path, value)
        return value

    def tree(self, sha):
        with self.lock:
            root = self.commit(sha)["tree"]
            if root in self._trees:
                return self._trees[root]
            cache = self._safe("trees/" + root + ".json")
            value = (
                parse(cache.read_bytes())
                if cache.exists()
                else self.api("git/trees/" + root + "?recursive=true")
            )
            if (
                value.get("sha") != root
                or value.get("truncated") is not False
                or not isinstance(value.get("tree"), list)
                or len(value["tree"]) > 100000
            ):
                raise SyncError(
                    "SOURCE_TREE_INCOMPLETE",
                    "上游目录不完整或超过预算，未发布；请缩小来源仓库",
                    503,
                )
            groups = {}
            rows = {}
            for item in value["tree"]:
                path = item.get("path")
                self.validate_path(path)
                self.validate_sha(item.get("sha"))
                mode, kind = item.get("mode"), item.get("type")
                if path in rows or (mode, kind) not in {
                    ("040000", "tree"),
                    ("100644", "blob"),
                    ("100755", "blob"),
                    ("120000", "blob"),
                    ("160000", "commit"),
                }:
                    raise SyncError("INVALID_TREE", "上游目录条目无效", 503)
                rows[path] = item
                parent = str(PurePosixPath(path).parent)
                groups.setdefault("" if parent == "." else parent, []).append(item)
            trees = {
                "": root,
                **{p: r["sha"] for p, r in rows.items() if r["type"] == "tree"},
            }
            if set(groups) - set(trees):
                raise SyncError("INVALID_TREE", "上游目录缺少父节点", 503)
            for directory, expected in trees.items():
                entries = sorted(
                    groups.get(directory, []),
                    key=lambda r: PurePosixPath(r["path"]).name.encode()
                    + (b"/" if r["type"] == "tree" else b""),
                )
                body = b"".join(
                    r["mode"].lstrip("0").encode()
                    + b" "
                    + PurePosixPath(r["path"]).name.encode()
                    + b"\0"
                    + bytes.fromhex(r["sha"])
                    for r in entries
                )
                if object_sha("tree", body) != expected:
                    raise SyncError(
                        "TREE_HASH_MISMATCH", "上游目录与固定提交不一致", 503
                    )
            if not cache.exists():
                write(cache, value)
            result = {
                p: r["sha"]
                for p, r in rows.items()
                if r["mode"] in ("100644", "100755")
            }
            if len(self._trees) >= 4:
                self._trees.pop(next(iter(self._trees)))
            self._trees[root] = result
            return result

    def blob(self, sha, path):
        self.validate_path(path)
        tree = self.tree(sha)
        key = tree.get(path)
        if key is None:
            raise SyncError("ASSET_NOT_FILE", "固定提交未提供对应普通文件", 404)
        target = self._safe("blobs/" + key)
        limit = (
            8 * 1024 * 1024
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
            else MAX_FILE
        )
        if target.exists():
            if target.stat().st_size > limit:
                raise SyncError("SOURCE_TOO_LARGE", "固定文件超出预算")
            data = target.read_bytes()
        else:
            data = self.api("git/blobs/" + key, limit, raw=True)
        if object_sha("blob", data) != key:
            raise SyncError("BLOB_HASH_MISMATCH", "上游内容与固定目录不一致", 503)
        if not target.exists():
            import os
            import uuid

            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name("." + uuid.uuid4().hex)
            try:
                with temp.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        return data

    def snapshot(self, sha, target):
        value = parse(self.blob(sha, "ptcg_chs_infos.json"))
        tree = self.tree(sha)
        write(Path(target) / "upstream.json", value)
        write(
            Path(target) / "source.json",
            {
                "repository": self.repository,
                "commit": sha,
                "tree": tree,
                "transport": "github-https-git-objects",
                "commitBinding": "github-tls",
            },
        )
        return value, tree

    def is_ancestor(self, previous, current):
        self.validate_sha(previous)
        self.validate_sha(current)
        if previous == current:
            return True
        value = self.api("compare/" + previous + "..." + current, MAX_TREE)
        return (
            value.get("base_commit", {}).get("sha") == previous
            and value.get("merge_base_commit", {}).get("sha") == previous
            and value.get("status") in ("ahead", "identical")
        )

    def cache_all(self, sha):
        tree = self.tree(sha)
        total = 0
        for path in tree:
            total += len(self.blob(sha, path))
            if total > 2 * 1024**3:
                raise SyncError(
                    "SOURCE_TOO_LARGE", "完整缓存超过 2 GiB 预算，已有资源保留", 503
                )
        return {
            "commit": sha,
            "objects": len(set(tree.values())),
            "missing": 0,
            "files": len(tree),
            "mode": "pinned-http-objects",
        }
