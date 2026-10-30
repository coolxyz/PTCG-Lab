"""Presentation metadata and local per-printing artwork overrides."""

import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from packages.collection.domain import ROOT, CARDS, DomainError

MAX_BYTES = 8 * 1024 * 1024


@lru_cache(maxsize=2)
def _details(stamp):
    path = ROOT / "data/catalog/card-details.json"
    return (
        json.loads(path.read_text(encoding="utf-8")).get("cards", {})
        if path.exists()
        else {}
    )


def details():
    path = ROOT / "data/catalog/card-details.json"
    return _details(path.stat().st_mtime_ns if path.exists() else 0)


class CardMedia:
    def __init__(self, store):
        self.store = store
        self.display_resources = None
        self.artwork = None
        self.directory = Path(store.path).parent / (
            Path(store.path).stem + "-card-images"
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        with store.db(write=True) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS card_images(printing_id TEXT PRIMARY KEY, filename TEXT NOT NULL)"
            )

    def decorate(self, cards):
        with self.store.db() as db:
            uploads = dict(db.execute("SELECT printing_id,filename FROM card_images"))
        source = details()
        overrides = self.display_resources.overrides() if self.display_resources else {}
        result = []
        for card in cards:
            pid = card["printingId"]
            info = source.get(pid, {})
            c = {
                **card,
                "chineseDetails": {
                    k: info[k]
                    for k in (
                        "sections",
                        "source",
                        "revision",
                        "textStatus",
                        "imageGap",
                    )
                    if k in info
                },
            }
            if not c["image"].get("url") and info.get("image"):
                c["image"] = info["image"]
            c = self._artwork(c)
            c = self._display(c, overrides.get(pid))
            if pid in uploads and (self.directory / uploads[pid]).is_file():
                c["image"] = {
                    "url": "/uploaded-card-images/" + uploads[pid],
                    "label": "用户上传卡图",
                    "userUploaded": True,
                }
            result.append(c)
        return result

    @staticmethod
    def _display(card, resource):
        if not resource:
            return card
        c = {**card}
        if "cnName" in resource:
            c["cnName"] = resource["cnName"]
        if "sections" in resource:
            c["chineseDetails"] = {
                **c.get("chineseDetails", {}),
                "sections": resource["sections"],
                "textStatus": "approved-display-pack",
            }
        if image := resource.get("image"):
            c["image"] = {
                "url": "/api/sync/resource-images/"
                + resource["packId"]
                + "/"
                + image["sha256"],
                "label": "已安装展示资源",
            }
        return c

    def _artwork(self, card):
        # A current approved sync snapshot takes precedence over the bundled
        # printing map. ZIP resources and personal uploads are applied later.
        if self.artwork:
            local = self.artwork.url(card)
            if local:
                return {**card, "image": {"url": local, "label": "在线卡图 · 本机缓存"}}
        return card

    def display(self, cards):
        overrides = self.display_resources.overrides() if self.display_resources else {}
        with self.store.db() as db:
            uploads = dict(db.execute("SELECT printing_id,filename FROM card_images"))
        result = []
        for card in cards:
            c = self._display(self._artwork(card), overrides.get(card["printingId"]))
            pid = card["printingId"]
            if pid in uploads and (self.directory / uploads[pid]).is_file():
                c = {**c, "image": {"url": "/uploaded-card-images/" + uploads[pid],
                    "label": "用户上传卡图", "userUploaded": True}}
            result.append(c)
        return result

    def upload(self, pid, data):
        if pid not in CARDS:
            raise DomainError("UNKNOWN_PRINTING", "卡牌版本不存在", 404)
        if len(data) > MAX_BYTES:
            raise DomainError("TOO_LARGE", "卡图不能超过 8 MB", 413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    if (
                        source.format not in ("PNG", "JPEG", "WEBP")
                        or source.width * source.height > 20_000_000
                    ):
                        raise ValueError("unsupported image")
                    source.load()
                    normalized = ImageOps.exif_transpose(source).convert("RGB")
                    normalized.thumbnail((1600, 2200))
                    output = io.BytesIO()
                    normalized.save(output, format="JPEG", quality=92)
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            raise DomainError(
                "INVALID_IMAGE",
                "请选择有效的 JPG、PNG 或 WebP 卡图（不超过 2000 万像素）",
                422,
            )
        data = output.getvalue()
        filename = hashlib.sha256(data).hexdigest() + ".jpg"
        (self.directory / filename).write_bytes(data)
        with self.store.db(write=True) as db:
            db.execute(
                "INSERT INTO card_images VALUES(?,?) ON CONFLICT(printing_id) DO UPDATE SET filename=excluded.filename",
                (pid, filename),
            )

    def remove(self, pid):
        if pid not in CARDS:
            raise DomainError("UNKNOWN_PRINTING", "卡牌版本不存在", 404)
        with self.store.db(write=True) as db:
            db.execute("DELETE FROM card_images WHERE printing_id=?", (pid,))
