"""Product adapter checks with approved local catalog, fresh SQLite, fake remote I/O."""

import json
from types import SimpleNamespace
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not (ROOT / "data/catalog/catalog.json").exists(),
    reason="Approved product catalog required",
)


@pytest.fixture
def product(tmp_path):
    from apps.api.product_online import ProductOnline
    from packages.collection.store import Store
    from packages.collection.card_media import CardMedia
    from packages.battle.presets import PRESETS
    from ptcg_online_client.transport import Server
    from ptcg_online_client.contracts import ClientError

    store = Store(tmp_path / "product.sqlite")
    deck = store.create_deck("Only local private name", PRESETS[0]["entries"])
    revision = store.freeze(deck["id"], deck["version"])
    adapter = ProductOnline(store, CardMedia(store))

    class Client:
        user = "u1"
        uncertain = False
        calls = []

        def status(self):
            return {
                "identity": {"serverId": "s1", "userId": self.user, "deviceId": "d1"},
                "reauthenticationRequired": False,
            }

        def submit_deck(self, entries, rule, *, request_id):
            self.calls.append(
                {"entries": entries, "ruleReleaseId": rule, "requestId": request_id}
            )
            if self.uncertain:
                raise ClientError("CONNECTION_UNKNOWN", retryable=True)
            return {
                "deckRevision": "remote-" + str(len(self.calls)),
                "ruleReleaseId": rule,
            }

    profile = SimpleNamespace(client=Client(), server=Server("https://example.test"))
    return store, adapter, deck, revision, profile


def body(revision, request="request-1", rule="rule-1"):
    return {"revisionId": revision["id"], "ruleReleaseId": rule, "requestId": request}


def test_shared_snapshot_mapping_and_identity_rule_server_isolation(product):
    from ptcg_online_client.transport import Server

    store, adapter, deck, revision, profile = product
    result = adapter.call(profile, "product-deck", body(revision))
    assert adapter.call(profile, "product-deck", body(revision, "request-2")) == result
    assert len(profile.client.calls) == 1
    sent = profile.client.calls[0]
    assert set(sent) == {"entries", "ruleReleaseId", "requestId"}
    assert deck["id"] not in json.dumps(sent) and deck["name"] not in json.dumps(sent)
    assert sent["entries"] == revision["entries"]
    adapter.call(profile, "product-deck", body(revision, "request-3", "rule-2"))
    profile.client.user = "u2"
    adapter.call(profile, "product-deck", body(revision, "request-1"))
    profile.server = Server("https://other.example")
    adapter.call(profile, "product-deck", body(revision, "request-1"))
    assert len(profile.client.calls) == 4
    with store.db() as db:
        assert (
            db.execute("SELECT count(*) FROM online_deck_requests").fetchone()[0] == 5
        )
    assert Path(store.path + ".before-product-online.sqlite").exists()


def test_unknown_submission_survives_edit_delete_and_restart(product):
    from apps.api.product_online import ProductOnline
    from packages.collection.card_media import CardMedia
    from ptcg_online_client.contracts import ClientError
    from ptcg_desktop_api.bridge import BridgeError

    store, adapter, deck, revision, profile = product
    profile.client.uncertain = True
    with pytest.raises(ClientError):
        adapter.call(profile, "product-deck", body(revision))
    assert adapter.pending(profile) == [body(revision)]
    changed = store.save_deck(deck["id"], "Edited", [], deck["version"])
    store.delete_revision(deck["id"], revision["id"], changed["version"])
    store.delete_deck(deck["id"], changed["version"])
    restarted = ProductOnline(store, CardMedia(store))
    profile.client.uncertain = False
    restarted.call(profile, "product-deck", body(revision))
    assert profile.client.calls[0] == profile.client.calls[1]
    assert not restarted.pending(profile)
    with pytest.raises(BridgeError, match="REVISION_NOT_FOUND"):
        restarted.call(profile, "product-deck", body(revision, "new"))
    with pytest.raises(BridgeError, match="REQUEST_ID_CONFLICT"):
        restarted.call(profile, "product-deck", body(revision, rule="different"))


def test_freeze_detects_concurrent_edit_and_revision_corruption(product):
    from ptcg_desktop_api.bridge import BridgeError

    store, adapter, deck, revision, profile = product
    store.save_deck(deck["id"], "Changed", [], deck["version"])
    with pytest.raises(BridgeError, match="VERSION_CONFLICT"):
        adapter.call(
            profile,
            "product-freeze",
            {"deckId": deck["id"], "expectedVersion": deck["version"]},
        )
    with store.db(True) as db:
        db.execute("UPDATE revisions SET body='{}' WHERE id=?", (revision["id"],))
    with pytest.raises(BridgeError, match="REVISION_CORRUPT"):
        adapter.call(profile, "product-deck", body(revision))


def test_resources_require_exact_catalog_and_snapshot_uploaded_image(product):
    from ptcg_desktop_api.resources import Resources
    from PIL import Image
    import io

    store, adapter, _, revision, _ = product
    output = io.BytesIO()
    Image.new("RGB", (10, 14), "blue").save(output, format="PNG")
    pid = revision["entries"][0]["printingId"]
    adapter.media.upload(pid, output.getvalue())
    resources = adapter.resources(Resources(Path(store.path).parent / "cache"))
    assert resources.catalog("wrong-version", "unavailable")["missing"]
    catalog = resources.catalog(resources.version, "unavailable")
    image = next(c for c in catalog["cards"] if c["printingId"] == pid)["image"]["url"]
    assert image.startswith("/resources/")
    before = resources.image(image.split("/")[-1])
    adapter.media.remove(pid)
    for p in adapter.media.directory.iterdir():
        p.unlink()
    assert resources.image(image.split("/")[-1]) == before
    assert resources.catalog(resources.version, catalog["assetVersion"]) == catalog
    assert resources.catalog(resources.version, "unknown-assets")["missing"]


def test_persisted_resource_version_survives_adapter_restart(product):
    from ptcg_desktop_api.resources import Resources

    store, adapter, *_ = product
    cache = Path(store.path).parent / "cache"
    first = adapter.resources(Resources(cache))
    catalog = first.catalog(first.version, "unavailable")
    restarted = adapter.resources(Resources(cache))
    assert restarted.catalog(first.version, catalog["assetVersion"]) == catalog


def test_source_cache_rejects_modified_image_bytes(tmp_path):
    from packages.sync.images import Images
    from packages.sync.common import SyncError
    import sqlite3

    images = Images(tmp_path, SimpleNamespace(validate_sha=lambda value: None))
    digest = "a" * 64
    (images.directory / digest).write_bytes(b"tampered")
    with sqlite3.connect(images.database) as db:
        db.execute(
            "INSERT INTO images VALUES(?,?,?,?)", ("c" * 40, "1", digest, "image/png")
        )
    with pytest.raises(SyncError, match="缓存卡图校验失败"):
        images.get("c" * 40, "1")
