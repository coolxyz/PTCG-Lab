"""Actual local product routes share lazy artwork and preserve ZIP/upload priority."""

from hashlib import sha256
import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from apps.api.main import create_app
from apps.api.product_online import ProductResources
from packages.collection.domain import CARDS, RAW, ROOT
from packages.sync.http_source import object_sha
from ptcg_desktop_api.resources import Resources
from ptcg_engine_provider.catalog import MemoryCatalog


def test_lazy_catalog_shared_views_cached_http_and_offline(
    tmp_path, monkeypatch
):
    import packages.sync.artwork as module

    buffer = io.BytesIO()
    Image.new("RGB", (6, 8), (80, 30, 130)).save(buffer, format="PNG")
    data = buffer.getvalue()
    card = next(
        c
        for c in CARDS.values()
        if c["effectStatus"] == "verified" and c.get("engineId")
    )
    row = {
        "printingId": card["printingId"],
        "engineId": card["engineId"],
        "path": "img/1.png",
        "blob": object_sha("blob", data),
    }
    index = tmp_path / "builtin.json"
    index.write_text(
        json.dumps(
            {
                "schema": 1,
                "repository": "https://github.com/example/Cards",
                "commit": "a" * 40,
                "catalogFileSha256": sha256(
                    (ROOT / "data/catalog/catalog.json").read_bytes()
                ).hexdigest(),
                "cards": [row],
            }
        )
    )
    monkeypatch.setattr(module, "INDEX", str(index))
    import packages.sync.builtin_images as face_module
    monkeypatch.setattr(face_module, "INDEX", str(tmp_path / "no-builtin-faces.json"))
    app = create_app(tmp_path.resolve() / "app.sqlite")
    calls = []
    app.state.sync.artwork_cache.transport = (
        lambda url, limit: calls.append(url) or data
    )
    with TestClient(app) as client:
        cards = client.get("/api/cards").json()["cards"]
        shown = next(c for c in cards if c["printingId"] == card["printingId"])
        url = shown["image"]["url"]
        assert url.startswith("/api/sync/artwork/") and not calls
        response = client.get(url)
        assert (
            response.content == data
            and "immutable" in response.headers["Cache-Control"]
        )
        assert client.get(url).content == data and len(calls) == 1
        session = client.post("/api/battle/session").json()
        assert (
            next(c for c in session["cards"] if c["printingId"] == card["printingId"])[
                "image"
            ]["url"]
            == url
        )
        fallback = Resources(tmp_path.resolve() / "native-resources")
        online = ProductResources(app.state.card_media, fallback, app.state.sync.images)
        result = online.catalog(
            MemoryCatalog.from_catalog(RAW).catalog_version, "unavailable"
        )
        assert (
            next(c for c in result["cards"] if c["engineId"] == card["engineId"])[
                "image"
            ]["url"]
            == url
        )
        assert len(calls) == 1, (
            "Reading all display metadata must not fetch any artwork"
        )
        settings = client.get("/api/sync/artwork/settings")
        assert settings.status_code == 200
        assert "no-store" in settings.headers["Cache-Control"]
        assert settings.json()["cachedImages"] == 1
        assert (
            client.put(
                "/api/sync/artwork/settings",
                json={
                    "source": "cdn",
                    "baseUrl": "http://localhost",
                    "capacityMiB": 1024,
                },
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/sync/artwork/settings",
                json={
                    "source": "cdn",
                    "baseUrl": "https://images.example.org/ptcg",
                    "capacityMiB": 512,
                },
            ).status_code
            == 200
        )
        assert client.get(url).content == data and len(calls) == 1
        assert (
            client.put(
                "/api/sync/artwork/settings",
                json={"source": "github-raw", "baseUrl": "", "capacityMiB": 1024},
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )


def test_slow_image_queue_does_not_starve_product_metadata(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    app = create_app(tmp_path.resolve() / "app.sqlite")
    artwork = app.state.card_media.artwork
    key = next(iter(artwork.assets))
    started, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = [0]

    def blocked(_key):
        with lock:
            active[0] += 1
            if active[0] >= 2:
                started.set()
        assert release.wait(10)
        return b"fixture", "image/png"

    artwork.image = blocked
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=43) as pool:
        images = [
            pool.submit(client.get, "/api/sync/artwork/" + key) for _ in range(42)
        ]
        try:
            assert started.wait(3)
            metadata = pool.submit(client.get, "/api/sync/artwork/settings")
            assert metadata.result(timeout=2).status_code == 200
            assert active[0] == 2
        finally:
            release.set()
        assert all(f.result(timeout=5).status_code == 200 for f in images)


def test_artwork_api_reports_retry_policy_without_sharing_network_failure(tmp_path):
    from packages.sync.artwork import DownloadError
    app = create_app(tmp_path.resolve() / "app.sqlite")
    artwork = app.state.card_media.artwork
    key = next(iter(artwork.assets))
    def failed(_key):
        raise DownloadError("IMAGE_NETWORK_UNAVAILABLE", "single image failure", 2)
    artwork.image = failed
    with TestClient(app) as client:
        response = client.get("/api/sync/artwork/" + key)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "2"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["retryable"] is True
        assert response.json()["cooldownScope"] == "image"
        assert client.get("/api/sync/artwork/settings").json()["retryAfterSeconds"] == 0
