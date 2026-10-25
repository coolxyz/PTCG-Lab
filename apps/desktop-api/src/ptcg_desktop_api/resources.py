"""Pinned display-only catalogs and a bounded immutable raster cache."""

from hashlib import sha256
import http.client
import os
from pathlib import Path
import re
import ssl
from threading import Lock
from urllib.parse import urlsplit
import uuid
from ptcg_online_client.contracts import unpack
from .bridge import BridgeError

MAX_IMAGE = 2 * 1024 * 1024


def digest(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value)


def raster(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise BridgeError("INVALID_IMAGE")


class Resources:
    def __init__(self, cache, manifests=(), *, capacity=256 * 1024 * 1024):
        self.root = Path(cache)
        if self.root.is_symlink():
            raise ValueError("Unsafe cache")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_mode & 0o077:
            raise ValueError("Unsafe cache permissions")
        self.lock, self.capacity = Lock(), capacity
        self.catalogs, self.images = {}, {}
        for path, pin in manifests:
            self.add(path, pin)

    def add(self, path, pin):
        path = Path(path)
        if not digest(pin) or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Invalid manifest")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != pin:
            raise ValueError("Manifest hash mismatch")
        manifest = unpack(raw)
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"catalogVersion", "assetVersion", "cards"}
            or not isinstance(manifest["cards"], list)
            or len(manifest["cards"]) > 10000
        ):
            raise ValueError("Invalid manifest shape")
        for key in ("catalogVersion", "assetVersion"):
            if not isinstance(manifest[key], str) or not 1 <= len(manifest[key]) <= 128:
                raise ValueError("Invalid manifest version")
        key = (manifest["catalogVersion"], manifest["assetVersion"])
        if key in self.catalogs:
            raise ValueError("Duplicate immutable resource version")
        rows, images = [], {}
        for card in manifest["cards"]:
            if not isinstance(card, dict) or not {
                "engineId",
                "englishName",
                "cnName",
                "image",
            } == set(card):
                raise ValueError("Invalid display card")
            if any(
                not isinstance(card[k], str) or not 1 <= len(card[k]) <= 256
                for k in ("engineId", "englishName", "cnName")
            ):
                raise ValueError("Invalid display text")
            source = card["image"]
            if source is not None:
                if (
                    not isinstance(source, dict)
                    or set(source) != {"sha256", "url"}
                    or not digest(source["sha256"])
                ):
                    raise ValueError("Invalid image source")
                if source["url"] is not None:
                    url = urlsplit(source["url"])
                    if (
                        url.scheme != "https"
                        or not url.hostname
                        or url.username
                        or url.password
                        or url.fragment
                        or url.query
                    ):
                        raise ValueError("Image source requires fixed HTTPS URL")
                images[source["sha256"]] = (path.parent.resolve(), source["url"])
            rows.append(
                {
                    "engineId": card["engineId"],
                    "englishName": card["englishName"],
                    "cnName": card["cnName"],
                    "effectStatus": "verified",
                    "reviewNote": "",
                    "image": {
                        "url": "/resources/" + source["sha256"] if source else None,
                        "label": "本地显示资源",
                    },
                }
            )
        self.catalogs[key] = {
            "catalogVersion": key[0],
            "assetVersion": key[1],
            "manifestSha256": pin,
            "cards": rows,
        }
        self.images.update(images)
        # Versioned metadata is retained; loading a new manifest never deletes old blobs.
        self._write("manifest-" + pin + ".json", raw)

    def catalog(self, catalog_version, asset_version):
        candidates = [
            v
            for (c, a), v in self.catalogs.items()
            if c == catalog_version
            and (a == asset_version or asset_version == "unavailable")
        ]
        return (
            candidates[-1]
            if candidates
            else {
                "catalogVersion": catalog_version,
                "assetVersion": asset_version,
                "cards": [],
                "missing": True,
            }
        )

    def _write(self, name, data):
        target = self.root / name
        if target.is_symlink():
            raise BridgeError("INVALID_IMAGE")
        previous_size = target.stat().st_size if target.is_file() else 0
        if (
            previous_size == len(data)
            and target.is_file()
            and target.read_bytes() == data
        ):
            return
        if (
            sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
            - previous_size
            + len(data)
            > self.capacity
        ):
            raise BridgeError("CACHE_CAPACITY", 503)
        temp = self.root / ("." + uuid.uuid4().hex)
        try:
            with temp.open("xb") as out:
                os.chmod(temp, 0o600)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.root / name)
        finally:
            temp.unlink(missing_ok=True)

    def image(self, key):
        if not digest(key):
            raise BridgeError("IMAGE_NOT_FOUND", 404)
        with self.lock:
            cached = self.root / key
            if cached.is_symlink():
                raise BridgeError("INVALID_IMAGE")
            if cached.is_file():
                data = (
                    cached.read_bytes() if cached.stat().st_size <= MAX_IMAGE else b""
                )
                if sha256(data).hexdigest() != key:
                    raise BridgeError("INVALID_IMAGE")
                return data, raster(data)
            if key not in self.images:
                raise BridgeError("IMAGE_NOT_FOUND", 404)
            root, url = self.images[key]
            local = root / "files" / key
            if (
                local.is_file()
                and not local.is_symlink()
                and local.resolve().is_relative_to(root)
                and local.stat().st_size <= MAX_IMAGE
            ):
                data = local.read_bytes()
            elif url:
                target = urlsplit(url)
                conn = http.client.HTTPSConnection(
                    target.hostname,
                    target.port,
                    timeout=5,
                    context=ssl.create_default_context(),
                )
                try:
                    conn.request(
                        "GET",
                        target.path or "/",
                        headers={"Accept-Encoding": "identity"},
                    )
                    reply = conn.getresponse()
                    if (
                        reply.status != 200
                        or reply.getheader("Content-Encoding", "identity") != "identity"
                    ):
                        raise BridgeError("IMAGE_UNAVAILABLE", 404)
                    data = reply.read(MAX_IMAGE + 1)
                finally:
                    conn.close()
            else:
                raise BridgeError("IMAGE_UNAVAILABLE", 404)
            if len(data) > MAX_IMAGE or sha256(data).hexdigest() != key:
                raise BridgeError("INVALID_IMAGE")
            mime = raster(data)
            self._write(key, data)
            return data, mime
