"""Lazy, verified raster downloads shared by library, practice and online play.

Only the source index chooses repository/commit/path/blob. The browser receives a
local immutable URL. Source settings never contain credentials or arbitrary paths.
"""

from contextlib import closing, contextmanager
from hashlib import sha256
import http.client
import ipaddress
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import threading
import time
import math
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlsplit
import uuid

import certifi

from .common import ROOT, SyncError, digest, read, write
from .raster import raster
from .http_source import HTTPSource, object_sha, parse
from .repositories import canonical

MAX_IMAGE = 8 * 1024 * 1024
DEFAULTS = {"source": "github-raw", "baseUrl": "", "capacityMiB": 1024, "localGitPath": ""}
INDEX = "resources/artwork/builtin-index.json"


def settings(value, *, validate_local=True):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS) or not {"source", "baseUrl", "capacityMiB"} <= set(value):
        raise SyncError("INVALID_IMAGE_SOURCE", "卡图来源设置格式无效")
    value = {**DEFAULTS, **value}
    if (
        value["source"] not in ("github-raw", "cdn", "local-git")
        or type(value["capacityMiB"]) is not int
        or not 64 <= value["capacityMiB"] <= 8192
        or not isinstance(value["baseUrl"], str)
        or not isinstance(value["localGitPath"], str)
        or len(value["localGitPath"]) > 4096
    ):
        raise SyncError("INVALID_IMAGE_SOURCE", "请选择卡图来源和 64–8192 MiB 缓存容量")
    base = value["baseUrl"].rstrip("/")
    if base:
        try:
            u = urlsplit(base)
            valid_port = u.port in (None, 443)
        except ValueError as exc:
            raise SyncError("INVALID_IMAGE_SOURCE", "静态图源地址格式无效") from exc
        if (
            u.scheme != "https"
            or not u.hostname
            or not valid_port
            or u.username is not None
            or u.password is not None
            or u.query
            or u.fragment
            or any(char in base for char in ("%", "?", "#"))
            or not base.isascii()
            or not re.fullmatch(r"[A-Za-z0-9.-]+", u.hostname)
            or "." not in u.hostname
            or ".." in u.path.split("/")
            or "//" in u.path
            or "\\" in base
            or any(ord(c) <= 32 for c in base)
        ):
            raise SyncError(
                "INVALID_IMAGE_SOURCE", "静态图源必须为无凭据、无参数的 HTTPS 地址"
            )
    if value["source"] == "cdn" and not base:
        raise SyncError("INVALID_IMAGE_SOURCE", "请填写静态图源的 HTTPS 基础地址")
    if value["source"] == "local-git" and validate_local:
        from .local_artwork import local_path
        value["localGitPath"] = str(local_path(value["localGitPath"]))
    return {**value, "baseUrl": base}


class DownloadError(SyncError):
    def __init__(self, code, message, retry_after=60, *, retryable=True,
                 scope=None, upstream_status=None, ray=None, status=503):
        super().__init__(code, message, status)
        self.retry_after = retry_after
        self.retryable = retryable
        self.scope = scope or ("source" if code == "IMAGE_SOURCE_BUSY" else "image")
        self.upstream_status, self.ray = upstream_status, ray


def retry_seconds(header, default=60):
    try:
        if header.isdecimal():
            return min(86401, max(1, int(header)))
        return min(86401, max(1, math.ceil(parsedate_to_datetime(header).timestamp() - time.time())))
    except (ValueError, TypeError, OverflowError):
        return default


def https(url, limit):
    """Use fixed-origin Raw TLS; pin public addresses for configurable CDN hosts."""
    u = urlsplit(url)
    if (
        u.scheme != "https"
        or not u.hostname
        or u.username is not None
        or u.password is not None
        or u.query
        or u.fragment
        or u.port not in (None, 443)
    ):
        raise SyncError("INVALID_IMAGE_SOURCE", "图片来源必须使用固定 HTTPS 地址")
    context = ssl.create_default_context(cafile=certifi.where())
    if u.hostname == "raw.githubusercontent.com":
        # This exact, fixed upstream sends no credentials or user-controlled
        # URLs. Keep hostname/certificate verification and normal DNS so VPNs
        # using synthetic DNS addresses work. Git blob validation still binds
        # every returned byte to the approved immutable printing index.
        connection = http.client.HTTPSConnection(
            u.hostname, timeout=15, context=context
        )
    else:
        addresses = socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)
        ips = {info[4][0] for info in addresses}
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise SyncError("INVALID_IMAGE_SOURCE", "静态图源必须解析到公开网络地址")

        class PinnedConnection(http.client.HTTPSConnection):
            def connect(self):
                connection = socket.create_connection(
                    (sorted(ips)[0], 443), self.timeout
                )
                try:
                    self.sock = self._context.wrap_socket(
                        connection, server_hostname=u.hostname
                    )
                except BaseException:
                    connection.close()
                    raise

        connection = PinnedConnection(u.hostname, timeout=15, context=context)
    try:
        connection.request(
            "GET",
            u.path,
            headers={
                "User-Agent": "PTCG-Lab-artwork/1",
                "Accept-Encoding": "identity",
                "Accept": "image/png,image/jpeg,image/webp",
            },
        )
        reply = connection.getresponse()
        header = reply.getheader("Retry-After", "")
        metadata = {"upstream_status": reply.status, "ray": reply.getheader("cf-ray")}
        if reply.status == 429 or reply.status == 503 and header:
            retry = retry_seconds(header)
            raise DownloadError(
                "IMAGE_SOURCE_BUSY", "卡图来源要求稍后重试，已暂停该图源请求", retry,
                retryable=retry <= 86400, scope="source", **metadata,
            )
        if reply.status in (404, 410):
            raise DownloadError("IMAGE_NOT_FOUND", "图源缺少这张卡图，请更新 CDN 图库", 300,
                                retryable=False, status=404, **metadata)
        if reply.status == 403:
            raise DownloadError("IMAGE_SOURCE_FORBIDDEN", "图源拒绝了这张卡图的访问，请检查访问策略", 300,
                                retryable=False, status=403, **metadata)
        if (
            reply.status != 200
            or reply.getheader("Content-Encoding", "identity") != "identity"
        ):
            raise DownloadError("IMAGE_UNAVAILABLE", "这张卡图暂不可用，其他图片继续加载", 5,
                                retryable=reply.status >= 500, **metadata)
        length = reply.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > limit):
            raise DownloadError("IMAGE_TOO_LARGE", "上游卡图超过单图容量限制", retryable=False)
        data, total, deadline = [], 0, time.monotonic() + 45
        while block := reply.read(min(65536, limit + 1 - total)):
            total += len(block)
            if total > limit:
                raise DownloadError("IMAGE_TOO_LARGE", "卡图下载超过单图容量限制", retryable=False)
            if time.monotonic() > deadline:
                raise DownloadError("IMAGE_NETWORK_UNAVAILABLE", "这张卡图下载超时，稍后自动重试", 2)
            data.append(block)
        return b"".join(data)
    except ssl.SSLCertVerificationError as exc:
        raise DownloadError("IMAGE_TLS_FAILED", "卡图来源证书验证失败", 300, retryable=False) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise DownloadError(
            "IMAGE_NETWORK_UNAVAILABLE", "这张卡图下载失败，其他图片继续加载", 2
        ) from exc
    finally:
        connection.close()


class ArtworkCache:
    def __init__(self, home, *, transport=None, clock=time.time):
        self.home = Path(home)
        self.directory = self.home / "artwork"
        if any(
            p.is_symlink() or p.is_junction()
            for p in (self.directory, *self.directory.parents)
        ):
            raise SyncError("UNSAFE_IMAGE_CACHE", "卡图缓存路径包含链接")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "index.sqlite"
        self.config = self.home / "artwork-settings.json"
        self.transport, self.clock = transport or https, clock
        self.lock, self.downloads = threading.RLock(), threading.Semaphore(2)
        self.flights = {}
        self.failures = {}
        self.host_cooldown = {}
        with closing(self.db()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS blobs(blob TEXT PRIMARY KEY,sha TEXT NOT NULL,mime TEXT NOT NULL,bytes INTEGER NOT NULL,used REAL NOT NULL)"
            )

    def db(self):
        self.safe(self.database)
        return sqlite3.connect(self.database, timeout=30)

    def safe(self, path):
        if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
            raise SyncError("UNSAFE_IMAGE_CACHE", "卡图缓存路径包含链接，未读取或覆盖")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise SyncError("UNSAFE_IMAGE_CACHE", "卡图缓存文件类型无效")
        return path

    def configuration(self):
        with self.lock:
            self.safe(self.config)
            if self.config.is_file():
                if self.config.stat().st_size > 32 * 1024:
                    raise SyncError("INVALID_IMAGE_SOURCE", "卡图来源设置过大")
                return settings(read(self.config), validate_local=False)
            return dict(DEFAULTS)

    def configure(self, value):
        value = settings(value)
        if value["source"] == "local-git":
            from .local_artwork import LocalGitArtwork
            LocalGitArtwork(value["localGitPath"]).validate()
        with self.lock:
            self.safe(self.config)
            write(self.config, value)
        return self.status()

    @contextmanager
    def singleflight(self, blob):
        with self.lock:
            pair = self.flights.setdefault(blob, [threading.Lock(), 0])
            pair[1] += 1
        try:
            with pair[0]:
                yield
        finally:
            with self.lock:
                pair[1] -= 1
                if not pair[1]:
                    self.flights.pop(blob, None)

    def status(self):
        config = self.configuration()
        available, message = self.local_status(config)
        with self.lock, closing(self.db()) as db:
            count, size = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM blobs"
            ).fetchone()
            host = urlsplit("https://raw.githubusercontent.com" if config["source"] == "github-raw" else config["baseUrl"]).hostname
            retry = 0 if config["source"] == "local-git" else max(0, self.host_cooldown.get(host, (0, None))[0] - self.clock())
        return {
            **config,
            "cachedImages": count,
            "cachedBytes": size,
            "retryAfterSeconds": max(0, int(retry)),
            "mode": "on-demand",
            "localGitAvailable": available,
            "localGitMessage": message,
        }

    @staticmethod
    def local_status(config):
        if config["source"] != "local-git":
            return None, ""
        from .local_artwork import LocalGitArtwork
        try:
            LocalGitArtwork(config["localGitPath"]).validate()
            return True, "本地目录可读取；卡图显示时逐张校验，不复制全库、不联网"
        except (SyncError, OSError) as error:
            return False, error.message if isinstance(error, SyncError) else "本地目录暂不可读取"

    def _cached(self, blob):
        with closing(self.db()) as db, db:
            row = db.execute(
                "SELECT sha,mime,bytes FROM blobs WHERE blob=?", (blob,)
            ).fetchone()
            if row:
                path = self.safe(self.directory / row[0])
                if path.is_file() and path.stat().st_size == row[2]:
                    data = path.read_bytes()
                    if (
                        len(data) <= MAX_IMAGE
                        and sha256(data).hexdigest() == row[0]
                        and object_sha("blob", data) == blob
                        and row[1] in ("image/png", "image/jpeg", "image/webp")
                    ):
                        db.execute(
                            "UPDATE blobs SET used=? WHERE blob=?", (self.clock(), blob)
                        )
                        return data, row[1]
                # Damaged or externally removed cache is never displayed. A valid
                # source can repair it atomically without changing any card identity.
                db.execute("DELETE FROM blobs WHERE blob=?", (blob,))
                if path.is_file():
                    path.unlink()
        return None

    def image(self, repository, commit, path, blob):
        repository = canonical(repository)
        HTTPSource.validate_sha(commit)
        HTTPSource.validate_sha(blob)
        HTTPSource.validate_path(path)
        with self.singleflight(blob):
            with self.lock:
                cached = self._cached(blob)
                config = self.configuration()
            if cached:
                return cached
            if config["source"] == "local-git":
                from .local_artwork import LocalGitArtwork
                with self.downloads:
                    return LocalGitArtwork(config["localGitPath"]).read(path, blob)
            base = (
                "https://raw.githubusercontent.com"
                if config["source"] == "github-raw"
                else config["baseUrl"]
            )
            url = (
                base
                + "/"
                + repository.removeprefix("https://github.com/")
                + "/"
                + commit
                + "/"
                + quote(path, safe="/")
            )
            host = urlsplit(url).hostname
            key = (url, blob)
            with self.downloads:
                with self.lock:
                    until, previous = max(
                        [self.host_cooldown.get(host, (0, None)), self.failures.get(key, (0, None))],
                        key=lambda item: item[0],
                    )
                    if previous is not None and until > self.clock():
                        raise DownloadError(
                            "IMAGE_RETRY_LATER",
                            "卡图请求已退避：" + previous.message,
                            max(1, math.ceil(until - self.clock())),
                            retryable=previous.retryable, scope=previous.scope,
                            upstream_status=previous.upstream_status, ray=previous.ray,
                            status=previous.status,
                        )
                try:
                    data = self.transport(url, MAX_IMAGE)
                    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE:
                        raise DownloadError("IMAGE_TOO_LARGE", "卡图超过单图容量限制", retryable=False)
                    if object_sha("blob", data) != blob:
                        raise DownloadError(
                            "IMAGE_HASH_MISMATCH", "卡图与固定提交不一致，未写入缓存", retryable=False
                        )
                    mime = raster(data)
                except (OSError, http.client.HTTPException) as exc:
                    error = DownloadError("IMAGE_NETWORK_UNAVAILABLE", "这张卡图下载失败，其他图片继续加载", 2)
                    self._failure(key, host, error)
                    raise error from exc
                except SyncError as exc:
                    error = exc if isinstance(exc, DownloadError) else DownloadError(
                        exc.code, exc.message, 300, retryable=False, status=exc.status
                    )
                    self._failure(key, host, error)
                    if error is exc:
                        raise
                    raise error from exc
                with self.lock:
                    self._store(blob, data, mime, config["capacityMiB"] * 1024**2)
                return data, mime

    def _failure(self, key, host, error):
        with self.lock:
            record = (self.clock() + error.retry_after, error)
            self.failures[key] = record
            if error.scope == "source":
                self.host_cooldown[host] = record
            if len(self.failures) > 512:
                self.failures.pop(next(iter(self.failures)))

    def _store(self, blob, data, mime, capacity):
        sha = sha256(data).hexdigest()
        output = self.safe(self.directory / sha)
        with closing(self.db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            referenced = {r[0] for r in db.execute("SELECT sha FROM blobs")}
            for file in self.directory.iterdir():
                if (
                    re.fullmatch(r"[a-f0-9]{64}", file.name)
                    and file.name not in referenced
                ):
                    self.safe(file).unlink(missing_ok=True)
            size = db.execute("SELECT COALESCE(SUM(bytes),0) FROM blobs").fetchone()[0]
            for old_blob, old_sha, old_bytes in db.execute(
                "SELECT blob,sha,bytes FROM blobs ORDER BY used,blob"
            ).fetchall():
                if size + len(data) <= capacity:
                    break
                self.safe(self.directory / old_sha).unlink(missing_ok=True)
                db.execute("DELETE FROM blobs WHERE blob=?", (old_blob,))
                size -= old_bytes
            temporary = self.directory / ("." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    import os

                    os.fsync(stream.fileno())
                temporary.replace(output)
                db.execute(
                    "INSERT OR REPLACE INTO blobs VALUES(?,?,?,?,?)",
                    (blob, sha, mime, len(data), self.clock()),
                )
            finally:
                temporary.unlink(missing_ok=True)


class BuiltinArtwork:
    def __init__(self, cache, *, root=ROOT, known=None, catalog_path=None, synced=None):
        self.cache, self.rows, self.assets = cache, {}, {}
        self.synced = synced
        index = Path(root) / INDEX
        if cache:
            cache.safe(index)
        if not index.exists():
            return
        if index.stat().st_size > 8 * 1024 * 1024:
            raise SyncError("INVALID_IMAGE_INDEX", "内置卡图索引过大")
        value = parse(index.read_bytes())
        if (
            set(value)
            != {"schema", "repository", "commit", "catalogFileSha256", "cards"}
            or type(value["schema"]) is not int
            or value["schema"] != 1
            or not isinstance(value["cards"], list)
            or len(value["cards"]) > 20000
            or sha256(
                Path(
                    catalog_path or Path(root) / "data/catalog/catalog.json"
                ).read_bytes()
            ).hexdigest()
            != value["catalogFileSha256"]
        ):
            raise SyncError("INVALID_IMAGE_INDEX", "内置卡图索引与随包目录不一致")
        if canonical(value["repository"]) != value["repository"]:
            raise SyncError("INVALID_IMAGE_INDEX", "内置卡图来源无效")
        HTTPSource.validate_sha(value["commit"])
        self.repository, self.commit = value["repository"], value["commit"]
        for row in value["cards"]:
            if set(row) != {"printingId", "engineId", "path", "blob"}:
                raise SyncError("INVALID_IMAGE_INDEX", "卡图索引条目无效")
            pid = row["printingId"]
            if (
                not isinstance(pid, str)
                or pid in self.rows
                or (
                    known is not None
                    and (
                        pid not in known
                        or row["engineId"] != known[pid].get("engineId")
                    )
                )
            ):
                raise SyncError("INVALID_IMAGE_INDEX", "内置卡图身份不一致")
            HTTPSource.validate_path(row["path"])
            HTTPSource.validate_sha(row["blob"])
            key = digest({"repository": self.repository, "commit": self.commit, **row})
            self.rows[pid] = (row["engineId"], key)
            self.assets[key] = row

    def url(self, card):
        current = card.get("image", {}).get("url") or ""
        synced = re.fullmatch(r"/api/sync/images/([a-f0-9]{40})/([0-9]+)", current)
        # Bundled display metadata can refer to an old sync URL while a fresh
        # profile has no such snapshot. Only an installed snapshot supersedes
        # the shipped printing index; missing snapshots must not hide its image.
        if synced and self.synced and self.synced.installed(synced[1]) and self.synced.available(synced[1], synced[2]):
            return current
        row = self.rows.get(card.get("printingId"))
        if row and row[0] == card.get("engineId"):
            return "/api/sync/artwork/" + row[1]
        if synced and self.synced and self.synced.available(synced[1], synced[2]):
            return current

    def image(self, key):
        row = self.assets.get(key)
        if row is None:
            raise SyncError("IMAGE_MISSING", "该版本没有匹配卡图", 404)
        return self.cache.image(self.repository, self.commit, row["path"], row["blob"])
