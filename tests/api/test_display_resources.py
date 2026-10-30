"""Actual product API integration: display packs never change game eligibility."""

from hashlib import sha256
import io
import json
import zipfile

from fastapi.testclient import TestClient
from PIL import Image


def make_pack(catalog, pid, engine, label="展示新名称"):
    stream = io.BytesIO()
    Image.new("RGB", (3, 3), (22, 44, 88)).save(stream, format="PNG")
    image = stream.getvalue()
    key = sha256(image).hexdigest()
    value = {
        "schema": 1,
        "kind": "ptcg-display-pack",
        "name": label,
        "catalogVersion": catalog,
        "cards": [
            {
                "printingId": pid,
                "engineId": engine,
                "cnName": label,
                "image": {"sha256": key, "size": len(image), "mime": "image/png"},
            }
        ],
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("DISPLAY.json", json.dumps(value, ensure_ascii=False))
        z.writestr("files/" + key, image)
    raw = out.getvalue()
    return raw, sha256(raw).hexdigest(), key


def test_display_install_activate_catalog_online_and_restart(tmp_path):
    from apps.api.main import create_app
    from apps.api.product_online import ProductResources
    from packages.collection.domain import RAW, CARDS
    from ptcg_engine_provider.catalog import MemoryCatalog
    from ptcg_desktop_api.resources import Resources

    app = create_app(tmp_path / "product.sqlite")
    catalog = MemoryCatalog.from_catalog(RAW).catalog_version
    card = next(c for c in RAW["cards"] if c.get("engineId"))
    pid = card["printingId"]
    original = dict(CARDS[pid])
    raw, pin, image = make_pack(catalog, pid, card["engineId"])
    with TestClient(app) as client:
        result = client.post(
            "/api/sync/resources/install",
            content=raw,
            headers={"Content-Type": "application/zip", "X-PTCG-Resource-SHA256": pin},
        )
        assert result.status_code == 201, result.text
        pack = result.json()["packId"]
        assert client.get("/api/sync/resources").json()["activePack"] is None
        assert (
            client.post(
                "/api/sync/resources/activate",
                json={"packId": pack, "expectedPack": None},
            ).status_code
            == 200
        )
        cards = client.get("/api/cards").json()["cards"]
        decorated = next(c for c in cards if c["printingId"] == pid)
        assert decorated["cnName"] == "展示新名称"
        url = decorated["image"]["url"]
        response = client.get(url)
        assert (
            response.status_code == 200
            and sha256(response.content).hexdigest() == image
        )
        assert CARDS[pid] == original
        fallback = Resources(tmp_path.resolve() / "native-cache")
        online = ProductResources(app.state.card_media, fallback, app.state.sync.images)
        first = online.catalog(catalog, "unavailable")
        # The effect may have multiple printings. All current display rows remain
        # semantic-catalog matched; any selected pack image stays a local route.
        assert first["catalogVersion"] == catalog
        assert (
            client.post(
                "/api/sync/resources/activate",
                json={"packId": None, "expectedPack": "0" * 64},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/sync/resources/activate",
                json={"packId": None, "expectedPack": pack},
            ).status_code
            == 200
        )
        assert client.get(url).status_code == 200
        assert online.catalog(catalog, first["assetVersion"]) == first
        assert (
            client.post(
                "/api/sync/resources/activate",
                json={"packId": pack, "expectedPack": None},
            ).status_code
            == 200
        )
    again = create_app(tmp_path / "product.sqlite")
    with TestClient(again) as client:
        assert client.get("/api/sync/resources").json()["activePack"] == pack
        assert client.get(url).status_code == 200


def test_upload_requires_pin_rejects_cross_origin_and_preserves_state(tmp_path):
    from apps.api.main import create_app

    with TestClient(create_app(tmp_path / "p.sqlite")) as client:
        assert (
            client.post(
                "/api/sync/resources/install",
                content=b"broken",
                headers={"Content-Type": "application/zip"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/sync/resources/install",
                content=b"broken",
                headers={
                    "Content-Type": "application/zip",
                    "X-PTCG-Resource-SHA256": "0" * 64,
                    "Origin": "https://outside.test",
                },
            ).status_code
            == 403
        )
        assert client.get("/api/sync/resources").json()["packs"] == []


def test_update_source_cache_belongs_to_writable_profile(tmp_path):
    from apps.api.main import create_app

    app = create_app(tmp_path / "p.sqlite")
    try:
        assert app.state.sync.source.directory.is_relative_to(tmp_path)
    finally:
        app.state.sync.close()
