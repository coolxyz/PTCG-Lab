"""Immutable, display-only local packs with atomic installation and CAS activation.

No executable rules, user data, remote URLs or arbitrary files are accepted.
Old packs remain addressable for saved replay asset versions.
"""

from contextlib import closing
from hashlib import sha256
import io
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import zipfile

from PIL import Image
from .common import SyncError, now
from .http_source import parse

MAX_PACK = 256 * 1024 * 1024
MAX_UNPACKED = 512 * 1024 * 1024
HEX = re.compile("[a-f0-9]{64}")


def digest(value):
    return isinstance(value, str) and bool(HEX.fullmatch(value))


def safe(root, name):
    p = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or not name
        or p.is_absolute()
        or p.as_posix() != name
        or any(t in {".", ".."} or ":" in t or "\\" in t or "\0" in t for t in p.parts)
    ):
        raise SyncError("INVALID_RESOURCE_PACK", "资源文件路径无效")
    path = Path(root) / name
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise SyncError("INVALID_RESOURCE_PACK", "资源路径包含链接，未修改")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise SyncError("INVALID_RESOURCE_PACK", "资源文件类型无效")
    return path


def raster(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format not in ("PNG", "JPEG", "WEBP")
                or image.width * image.height > 20_000_000
            ):
                raise ValueError()
            mime = Image.MIME[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
        return mime
    except Exception as e:
        raise SyncError("INVALID_RESOURCE_IMAGE", "资源图片未通过完整图像校验") from e


def manifest(raw, catalog, known):
    if len(raw) > 16 * 1024 * 1024:
        raise SyncError("RESOURCE_CAPACITY", "资源清单过大")
    value = parse(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "kind", "name", "catalogVersion", "cards"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["kind"] != "ptcg-display-pack"
        or value["catalogVersion"] != catalog
    ):
        raise SyncError(
            "RESOURCE_CATALOG_MISMATCH",
            "展示资源必须匹配当前卡牌身份目录，不会替换对战规则",
            409,
        )
    if (
        not isinstance(value["name"], str)
        or not 1 <= len(value["name"]) <= 100
        or not isinstance(value["cards"], list)
        or not 1 <= len(value["cards"]) <= 20000
    ):
        raise SyncError("INVALID_RESOURCE_PACK", "资源包名称或卡牌数量无效")
    seen = set()
    images = {}
    for row in value["cards"]:
        if (
            not isinstance(row, dict)
            or set(row) - {"printingId", "engineId", "cnName", "sections", "image"}
            or not {"printingId", "engineId"} <= set(row)
        ):
            raise SyncError("INVALID_RESOURCE_PACK", "资源只能包含卡图和展示文字")
        pid = row["printingId"]
        if (
            not isinstance(pid, str)
            or pid in seen
            or pid not in known
            or row["engineId"] != known[pid].get("engineId")
        ):
            raise SyncError(
                "RESOURCE_IDENTITY_MISMATCH", "资源卡牌身份与当前目录不一致", 409
            )
        seen.add(pid)
        if "cnName" in row and (
            not isinstance(row["cnName"], str) or not 1 <= len(row["cnName"]) <= 256
        ):
            raise SyncError("INVALID_RESOURCE_PACK", "展示名称无效")
        if "sections" in row:
            if (
                not isinstance(row["sections"], list)
                or len(row["sections"]) > 30
                or any(
                    not isinstance(s, dict)
                    or set(s) != {"title", "text"}
                    or any(not isinstance(v, str) or len(v) > 8000 for v in s.values())
                    for s in row["sections"]
                )
            ):
                raise SyncError("INVALID_RESOURCE_PACK", "展示文字无效")
        image = row.get("image")
        if image is not None:
            if (
                not isinstance(image, dict)
                or set(image) != {"sha256", "mime", "size"}
                or not digest(image["sha256"])
                or type(image["size"]) is not int
                or not 1 <= image["size"] <= 8 * 1024 * 1024
                or image["mime"] not in {"image/png", "image/jpeg", "image/webp"}
            ):
                raise SyncError("INVALID_RESOURCE_IMAGE", "图片摘要、类型或大小无效")
            if image["sha256"] in images and images[image["sha256"]] != image:
                raise SyncError("INVALID_RESOURCE_IMAGE", "重复图片声明不一致")
            images[image["sha256"]] = image
    return value, images


class DisplayResources:
    def __init__(self, home, catalog, known, *, capacity=1024**3):
        self.home = Path(home).absolute()
        safe(self.home, "index.sqlite")
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.catalog, self.known, self.capacity = catalog, known, capacity
        self.path = self.home / "index.sqlite"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS packs(id TEXT PRIMARY KEY,name TEXT,catalog TEXT,installed TEXT,bytes INTEGER);CREATE TABLE IF NOT EXISTS active(singleton INTEGER PRIMARY KEY CHECK(singleton=1),id TEXT REFERENCES packs(id));INSERT OR IGNORE INTO active VALUES(1,NULL);"
            )

    def db(self):
        safe(self.home, "index.sqlite")
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @property
    def active_id(self):
        with closing(self.db()) as db:
            return db.execute("SELECT id FROM active WHERE singleton=1").fetchone()[0]

    def status(self):
        with closing(self.db()) as db:
            packs = [
                dict(
                    id=r[0],
                    name=r[1],
                    catalogVersion=r[2],
                    installedAt=r[3],
                    bytes=r[4],
                )
                for r in db.execute("SELECT * FROM packs ORDER BY installed,id")
            ]
            current = db.execute("SELECT id FROM active WHERE singleton=1").fetchone()[
                0
            ]
        return dict(
            activePack=current, packs=packs, capacity=self.capacity, displayOnly=True
        )

    def load(self, key, *, all_images=False, current=False):
        if not digest(key):
            raise SyncError("RESOURCE_NOT_FOUND", "资源版本不存在", 404)
        path = safe(self.home, "versions/" + key + "/DISPLAY.json")
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise SyncError("RESOURCE_CORRUPT", "资源清单缺失或损坏")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != key:
            raise SyncError("RESOURCE_CORRUPT", "资源清单身份校验失败")
        decoded = parse(raw)
        catalog = self.catalog if current else decoded.get("catalogVersion")
        known = (
            self.known
            if current
            else {
                r.get("printingId"): r
                for r in decoded.get("cards", [])
                if isinstance(r, dict) and isinstance(r.get("printingId"), str)
            }
        )
        value, images = manifest(raw, catalog, known)
        if all_images:
            for digest_, image in images.items():
                self._image(key, digest_, image)
        return value, images

    def _image(self, key, digest_, image):
        path = safe(self.home, "versions/" + key + "/files/" + digest_)
        if not path.is_file() or path.stat().st_size != image["size"]:
            raise SyncError("RESOURCE_CORRUPT", "资源图片缺失或损坏")
        data = path.read_bytes()
        if sha256(data).hexdigest() != digest_ or raster(data) != image["mime"]:
            raise SyncError("RESOURCE_CORRUPT", "资源图片摘要校验失败")
        return path, image["mime"]

    def image(self, key, digest_):
        if not digest(digest_):
            raise SyncError("RESOURCE_NOT_FOUND", "资源图片不存在", 404)
        _, images = self.load(key)
        if digest_ not in images:
            raise SyncError("RESOURCE_NOT_FOUND", "资源图片不属于此版本", 404)
        return self._image(key, digest_, images[digest_])

    def overrides(self):
        key = self.active_id
        if key is None:
            return {}
        value, _ = self.load(key, current=True)
        return {r["printingId"]: {**r, "packId": key} for r in value["cards"]}

    def install(self, bundle, pin):
        if not digest(pin):
            raise SyncError("INVALID_RESOURCE_PIN", "请提供发布方的完整 SHA-256")
        path = Path(bundle)
        if path.stat().st_size > MAX_PACK:
            raise SyncError("RESOURCE_CAPACITY", "资源 ZIP 超过 256 MiB")
        with path.open("rb") as stream:
            from hashlib import file_digest

            actual = file_digest(stream, "sha256").hexdigest()
        if actual != pin:
            raise SyncError("RESOURCE_HASH_MISMATCH", "资源 ZIP 与指定摘要不一致")
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                names = [i.filename for i in infos]
                if (
                    len(names) != len(set(names))
                    or not 1 <= len(names) <= 20001
                    or any(
                        i.compress_type
                        not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or i.is_dir()
                        or stat.S_ISLNK(i.external_attr >> 16)
                        or i.flag_bits & 1
                        for i in infos
                    )
                    or sum(i.file_size for i in infos) > MAX_UNPACKED
                ):
                    raise SyncError("INVALID_RESOURCE_PACK", "资源压缩包结构或大小无效")
                for name in names:
                    safe(self.home, name)
                info = archive.getinfo("DISPLAY.json")
                if info.file_size > 16 * 1024 * 1024:
                    raise SyncError("RESOURCE_CAPACITY", "资源清单过大")
                raw = archive.read(info)
                value, images = manifest(raw, self.catalog, self.known)
                key = sha256(raw).hexdigest()
                if set(names) != {"DISPLAY.json"} | {"files/" + h for h in images}:
                    raise SyncError(
                        "INVALID_RESOURCE_PACK", "资源包包含未声明文件或缺少图片"
                    )
                with closing(self.db()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    old = db.execute(
                        "SELECT id FROM packs WHERE id=?", (key,)
                    ).fetchone()
                    if old:
                        self.load(key, all_images=True, current=True)
                        return dict(
                            packId=key, alreadyInstalled=True, activePack=self.active_id
                        )
                    if (
                        db.execute("SELECT COUNT(*) FROM packs").fetchone()[0] >= 8
                        or db.execute(
                            "SELECT COALESCE(SUM(bytes),0) FROM packs"
                        ).fetchone()[0]
                        + sum(i.file_size for i in infos)
                        > self.capacity
                    ):
                        raise SyncError(
                            "RESOURCE_CAPACITY",
                            "资源存储已满；既有版本为回放保留，未自动删除",
                            409,
                        )
                    versions = self.home / "versions"
                    versions.mkdir(exist_ok=True)
                    if versions.is_symlink() or versions.is_junction():
                        raise SyncError("INVALID_RESOURCE_PACK", "资源路径包含链接")
                    with tempfile.TemporaryDirectory(
                        prefix=".install-", dir=versions
                    ) as temp:
                        stage = Path(temp)
                        with (stage / "DISPLAY.json").open("xb") as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        (stage / "files").mkdir()
                        for h, image in images.items():
                            item = archive.getinfo("files/" + h)
                            if item.file_size != image["size"]:
                                raise SyncError(
                                    "RESOURCE_HASH_MISMATCH", "图片大小与清单不一致"
                                )
                            data = archive.read(item)
                            if (
                                sha256(data).hexdigest() != h
                                or raster(data) != image["mime"]
                            ):
                                raise SyncError(
                                    "RESOURCE_HASH_MISMATCH", "图片与清单不一致"
                                )
                            with (stage / "files" / h).open("xb") as stream:
                                stream.write(data)
                                stream.flush()
                                os.fsync(stream.fileno())
                        target = versions / key
                        if target.exists():
                            raise SyncError(
                                "RESOURCE_ORPHAN",
                                "发现未登记的资源目录，请保留并检查，未覆盖",
                            )
                        stage.rename(target)
                    db.execute(
                        "INSERT INTO packs VALUES(?,?,?,?,?)",
                        (
                            key,
                            value["name"],
                            self.catalog,
                            now(),
                            sum(i.file_size for i in infos),
                        ),
                    )
                return dict(
                    packId=key, alreadyInstalled=False, activePack=self.active_id
                )
        except SyncError:
            raise
        except (zipfile.BadZipFile, KeyError, ValueError, RuntimeError) as e:
            raise SyncError(
                "INVALID_RESOURCE_PACK", "资源压缩包无法验证，当前版本保持不变"
            ) from e

    def activate(self, key, expected):
        if expected is not None and not digest(expected):
            raise SyncError("INVALID_RESOURCE_PIN", "当前资源版本无效")
        with closing(self.db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT id FROM active WHERE singleton=1").fetchone()[
                0
            ]
            if current != expected:
                raise SyncError(
                    "RESOURCE_CONFLICT", "资源版本已变化，请刷新后重试", 409
                )
            if key is not None:
                if (
                    not digest(key)
                    or not db.execute(
                        "SELECT 1 FROM packs WHERE id=?", (key,)
                    ).fetchone()
                ):
                    raise SyncError("RESOURCE_NOT_FOUND", "资源版本不存在", 404)
                self.load(key, all_images=True, current=True)
            db.execute("UPDATE active SET id=? WHERE singleton=1", (key,))
        return dict(activePack=key, previousPack=current, displayOnly=True)
