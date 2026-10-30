"""Focused artwork behavior; no live GitHub, user profile or LAN service."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import io
import json
import socket
import threading

from PIL import Image
import pytest

from packages.sync.artwork import (
    ArtworkCache,
    DEFAULTS,
    DownloadError,
    https,
    settings,
)
from packages.sync.common import SyncError
from packages.sync.http_source import HTTPSource, object_sha
from packages.sync.images import Images

REPO = "https://github.com/example/Cards"
COMMIT = "a" * 40


def png(color=(90, 35, 130)):
    stream = io.BytesIO()
    Image.new("RGB", (6, 8), color).save(stream, format="PNG")
    return stream.getvalue()


def test_raw_pinned_path_cache_restart_and_offline(tmp_path, monkeypatch):
    raw, calls = png(), []
    blob = object_sha("blob", raw)
    monkeypatch.setattr(
        HTTPSource, "api", lambda *a, **kw: pytest.fail("No image REST API")
    )
    cache = ArtworkCache(
        tmp_path.resolve(), transport=lambda url, limit: calls.append(url) or raw
    )
    assert cache.status()["cachedImages"] == 0 and not calls
    assert cache.image(REPO, COMMIT, "img/一 张.png", blob) == (raw, "image/png")
    assert calls == [
        f"https://raw.githubusercontent.com/example/Cards/{COMMIT}/img/%E4%B8%80%20%E5%BC%A0.png"
    ]
    reopened = ArtworkCache(
        cache.home, transport=lambda *a: pytest.fail("Cached image must be offline")
    )
    assert reopened.image(REPO, COMMIT, "img/一 张.png", blob)[0] == raw
    assert reopened.status()["cachedBytes"] == len(raw)


def test_cdn_configuration_and_same_image_reuses_cache(tmp_path):
    raw, calls = png(), []
    cache = ArtworkCache(
        tmp_path.resolve(), transport=lambda url, limit: calls.append(url) or raw
    )
    cache.configure(
        {**DEFAULTS, "source": "cdn", "baseUrl": "https://images.example.org/ptcg/"}
    )
    blob = object_sha("blob", raw)
    cache.image(REPO, COMMIT, "img/1.png", blob)
    assert calls == [
        f"https://images.example.org/ptcg/example/Cards/{COMMIT}/img/1.png"
    ]
    cache.configure(DEFAULTS)
    cache.image(REPO, COMMIT, "img/1.png", blob)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "base",
    [
        "http://example.org",
        "https://user:pass@example.org",
        "https://example.org/?token=secret",
        "https://example.org/#fragment",
        "https://example.org:444/",
        "https://localhost/",
        "https://example.org/../private",
        "https://example.org/%2e%2e",
        "https://example.org/\nsecret",
        "https://[bad",
        "https://example.org/#",
        "https://example.org/?",
        "https://example.org/图片",
    ],
)
def test_cdn_setting_rejects_unsafe_sources(base):
    with pytest.raises(SyncError):
        settings({**DEFAULTS, "source": "cdn", "baseUrl": base})


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1", "192.168.1.2"]
)
def test_transport_rejects_private_dns_and_rebinding_candidates(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (ip, 443))]
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **kw: pytest.fail("Private destination must not connect"),
    )
    with pytest.raises(SyncError, match="公开网络"):
        https("https://images.example.org/image.png", 8192)


def test_duplicate_image_requests_share_one_download(tmp_path):
    raw, calls = png(), []
    started, release = threading.Event(), threading.Event()

    def download(url, limit):
        calls.append(url)
        started.set()
        assert release.wait(3)
        return raw

    cache = ArtworkCache(tmp_path.resolve(), transport=download)
    blob = object_sha("blob", raw)
    with ThreadPoolExecutor(max_workers=6) as workers:
        futures = [
            workers.submit(cache.image, REPO, COMMIT, "img/1.png", blob)
            for _ in range(6)
        ]
        assert started.wait(2)
        release.set()
        assert all(f.result()[0] == raw for f in futures)
    assert len(calls) == 1 and not cache.flights


def test_source_throttle_backoff_is_shared_and_cached_files_remain_usable(tmp_path):
    now, calls, raw = [100.0], [], png()

    def download(url, limit):
        calls.append(url)
        if len(calls) > 1:
            raise DownloadError("IMAGE_SOURCE_BUSY", "busy", 120)
        return raw

    cache = ArtworkCache(tmp_path.resolve(), transport=download, clock=lambda: now[0])
    blob = object_sha("blob", raw)
    cache.image(REPO, COMMIT, "img/1.png", blob)
    other = object_sha("blob", png((1, 2, 3)))
    with pytest.raises(DownloadError) as exc:
        cache.image(REPO, COMMIT, "img/2.png", other)
    assert exc.value.retry_after == 120
    for path in ("img/2.png", "img/3.png"):
        with pytest.raises(DownloadError, match="退避"):
            cache.image(REPO, COMMIT, path, other)
    assert len(calls) == 2
    assert cache.image(REPO, COMMIT, "img/1.png", blob)[0] == raw
    now[0] += 121
    with pytest.raises(DownloadError):
        cache.image(REPO, COMMIT, "img/2.png", other)
    assert len(calls) == 3


@pytest.mark.parametrize("kind", ["hash", "invalid-raster", "oversize", "network"])
def test_bad_download_is_not_cached_and_is_not_retried_immediately(tmp_path, kind):
    raw, calls = png(), []
    expected = object_sha("blob", raw)
    if kind == "invalid-raster":
        raw = b"not a raster"
        expected = object_sha("blob", raw)
    elif kind == "hash":
        raw = png((2, 3, 4))
    elif kind == "oversize":
        raw = b"x" * (8 * 1024**2 + 1)

    def download(*a):
        calls.append(1)
        if kind == "network":
            raise OSError("network detail must not reach UI")
        return raw

    cache = ArtworkCache(tmp_path.resolve(), transport=download)
    with pytest.raises(SyncError):
        cache.image(REPO, COMMIT, "img/1.png", expected)
    with pytest.raises(DownloadError):
        cache.image(REPO, COMMIT, "img/1.png", expected)
    assert cache.status()["cachedImages"] == 0 and len(calls) == 1


def test_lru_eviction_repairs_corruption_and_does_not_touch_offline_resources(tmp_path):
    first, second = png(), png((11, 22, 33))
    b1, b2 = object_sha("blob", first), object_sha("blob", second)
    calls = []
    cache = ArtworkCache(
        tmp_path.resolve(), transport=lambda *a: calls.append(1) or first
    )
    offline = tmp_path / "display/retained.png"
    offline.parent.mkdir()
    offline.write_bytes(first)
    cache._store(b1, first, "image/png", len(first) + len(second) - 1)
    cache._store(b2, second, "image/png", len(first) + len(second) - 1)
    assert not (cache.directory / sha256(first).hexdigest()).exists()
    assert (
        cache.directory / sha256(second).hexdigest()
    ).is_file() and offline.read_bytes() == first
    cache.image(REPO, COMMIT, "img/1.png", b1)
    file = cache.directory / sha256(first).hexdigest()
    file.write_bytes(b"broken")
    assert cache.image(REPO, COMMIT, "img/1.png", b1)[0] == first
    assert len(calls) == 2 and offline.read_bytes() == first


def test_cache_refuses_symlinks_and_hardlinks(tmp_path):
    raw = png()
    blob = object_sha("blob", raw)
    cache = ArtworkCache(tmp_path.resolve(), transport=lambda *a: raw)
    cache.image(REPO, COMMIT, "img/1.png", blob)
    file = cache.directory / sha256(raw).hexdigest()
    file.unlink()
    file.symlink_to(tmp_path / "outside")
    with pytest.raises(SyncError):
        cache.image(REPO, COMMIT, "img/1.png", blob)
    assert not (tmp_path / "outside").exists()


def test_sync_snapshot_uses_shared_raw_cache_and_never_calls_blob_api(tmp_path):
    raw, calls = png(), []
    blob = object_sha("blob", raw)

    class Source:
        repository = REPO

        def blob(self, *a):
            pytest.fail("REST/Git image fetch must not run")

    home = tmp_path.resolve()
    snap = home / "snapshots" / COMMIT
    snap.mkdir(parents=True)
    (snap / "image-index.json").write_text(
        json.dumps({"1": {"path": "img/1.png", "blob": blob}})
    )
    cache = ArtworkCache(home, transport=lambda *a: calls.append(1) or raw)
    images = Images(home, Source(), cache=cache)
    assert images.data(COMMIT, "1") == (raw, "image/png")
    assert images.get(COMMIT, "1")[0].read_bytes() == raw and len(calls) == 1
    assert images.status(COMMIT)["cached"] == 1


def test_http_does_not_follow_redirects_and_honors_retry_after(monkeypatch):
    import http.client

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    )
    requests = []
    status = [302]

    class Reply:
        def __init__(self):
            self.status = status[0]

        def getheader(self, name, default=None):
            return "120" if name == "Retry-After" else default

    class Connection:
        def __init__(self, *a, **k):
            pass

        def request(self, *a, **k):
            requests.append(a)

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    with pytest.raises(DownloadError):
        https("https://images.example.org/img.png", 8192)
    status[0] = 429
    with pytest.raises(DownloadError) as exc:
        https("https://images.example.org/img.png", 8192)
    assert exc.value.retry_after == 120 and len(requests) == 2


def test_builtin_index_identity_and_missing_snapshot_fallback(tmp_path):
    import packages.sync.artwork as module

    root = tmp_path.resolve()
    catalog = root / "data/catalog/catalog.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("{}")
    row = {
        "printingId": "p1",
        "engineId": "e1",
        "path": "img/1.png",
        "blob": object_sha("blob", png()),
    }
    value = {
        "schema": 1,
        "repository": REPO,
        "commit": COMMIT,
        "catalogFileSha256": sha256(catalog.read_bytes()).hexdigest(),
        "cards": [row],
    }
    file = root / module.INDEX
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps(value))
    cache = ArtworkCache(
        root / "profile", transport=lambda *a: pytest.fail("Metadata is lazy")
    )

    class Source:
        repository = REPO

    synced = Images(cache.home, Source(), cache=cache)
    builtin = module.BuiltinArtwork(
        cache, root=root, known={"p1": {"engineId": "e1"}}, synced=synced
    )
    card = {
        "printingId": "p1",
        "engineId": "e1",
        "image": {"url": f"/api/sync/images/{COMMIT}/1"},
    }
    assert builtin.url(card).startswith("/api/sync/artwork/")
    snap = cache.home / "snapshots" / COMMIT
    snap.mkdir(parents=True)
    (snap / "image-index.json").write_text(json.dumps({"1": row}))
    assert builtin.url(card) is None  # installed snapshot takes precedence
    assert builtin.url({**card, "engineId": "wrong"}) is None
    with pytest.raises(SyncError):
        module.BuiltinArtwork(cache, root=root, known={"p1": {"engineId": "wrong"}})
    catalog.write_text('{"changed":true}')
    with pytest.raises(SyncError):
        module.BuiltinArtwork(cache, root=root)


def test_fixed_raw_origin_keeps_verified_tls_with_vpn_synthetic_dns(monkeypatch):
    import http.client
    import ssl

    data = png()
    calls = []

    class Reply:
        status = 200

        def __init__(self):
            self.stream = io.BytesIO(data)

        def getheader(self, name, default=None):
            return default

        def read(self, size):
            return self.stream.read(size)

    class Connection:
        def __init__(self, host, *, timeout, context):
            assert host == "raw.githubusercontent.com"
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

        def request(self, method, path, headers):
            calls.append((method, path))

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: pytest.fail(
            "Raw uses normal TLS hostname resolution, not custom CDN address pinning"
        ),
    )
    assert (
        https(
            f"https://raw.githubusercontent.com/example/Cards/{COMMIT}/img/1.png", 8192
        )
        == data
    )
    assert calls == [("GET", f"/example/Cards/{COMMIT}/img/1.png")]
