"""Display revisions preserve rule identity and survive rejected installations."""

from hashlib import sha256
import io
import json
import stat
import zipfile

from PIL import Image
import pytest
from packages.sync.display import DisplayResources
from packages.sync.common import SyncError


def pack(tmp_path, name="first", *, change=None):
    image = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(image, format="PNG")
    raw = image.getvalue()
    h = sha256(raw).hexdigest()
    manifest = {
        "schema": 1,
        "kind": "ptcg-display-pack",
        "name": name,
        "catalogVersion": "catalog",
        "cards": [
            {
                "printingId": "p1",
                "engineId": "effect",
                "cnName": name,
                "image": {"sha256": h, "size": len(raw), "mime": "image/png"},
                "sections": [{"title": "招式", "text": "中文展示"}],
            }
        ],
    }
    files = {"files/" + h: raw}
    if change:
        change(manifest, files)
    path = tmp_path / (name + ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("DISPLAY.json", json.dumps(manifest, ensure_ascii=False))
        for name, data in files.items():
            archive.writestr(name, data)
    return path, sha256(path.read_bytes()).hexdigest(), h


@pytest.fixture
def resources(tmp_path):
    return DisplayResources(
        tmp_path.resolve() / "display", "catalog", {"p1": {"engineId": "effect"}}
    )


def test_install_switch_cas_history_and_restart(tmp_path, resources):
    first, pin, h = pack(tmp_path)
    key = resources.install(first, pin)["packId"]
    assert resources.active_id is None
    assert resources.install(first, pin)["alreadyInstalled"]
    resources.activate(key, None)
    second, pin2, _ = pack(tmp_path, "second")
    new = resources.install(second, pin2)["packId"]
    with pytest.raises(SyncError, match="变化"):
        resources.activate(new, None)
    assert resources.active_id == key
    resources.activate(new, key)
    assert resources.overrides()["p1"]["cnName"] == "second"
    assert resources.image(key, h)[0].is_file()
    reopened = DisplayResources(resources.home, "catalog", resources.known)
    assert reopened.active_id == new
    reopened.activate(key, new)
    assert reopened.overrides()["p1"]["cnName"] == "first"
    reopened.activate(None, key)
    assert reopened.overrides() == {}


@pytest.mark.parametrize(
    "kind",
    [
        "pin",
        "rule",
        "catalog",
        "identity",
        "path",
        "extra",
        "hash",
        "mime",
        "size",
        "text",
        "duplicate",
        "link",
    ],
)
def test_rejected_pack_does_not_change_active_or_install_partial(
    tmp_path, resources, kind
):
    first, pin, h = pack(tmp_path)
    old = resources.install(first, pin)["packId"]
    resources.activate(old, None)

    def mutate(m, files):
        if kind == "rule":
            m["cards"][0]["effectStatus"] = "verified"
        elif kind == "catalog":
            m["catalogVersion"] = "other"
        elif kind == "identity":
            m["cards"][0]["engineId"] = "different-rule"
        elif kind == "path":
            files["../escape"] = b"bad"
        elif kind == "extra":
            files["executable.py"] = b"bad"
        elif kind == "hash":
            files["files/" + h] = b"corrupt"
        elif kind == "mime":
            m["cards"][0]["image"]["mime"] = "image/jpeg"
        elif kind == "size":
            m["cards"][0]["image"]["size"] = 10**10
        elif kind == "text":
            m["cards"][0]["sections"] = [{"title": "x", "html": "<script>"}]
        elif kind == "duplicate":
            m["cards"].append(dict(m["cards"][0]))

    candidate, pin2, _ = pack(tmp_path, "rejected", change=mutate)
    if kind == "pin":
        pin2 = "0" * 64
    if kind == "link":
        with zipfile.ZipFile(candidate, "a") as z:
            entry = zipfile.ZipInfo("symlink")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(entry, "files/" + h)
        pin2 = sha256(candidate.read_bytes()).hexdigest()
    with pytest.raises(SyncError):
        resources.install(candidate, pin2)
    assert resources.active_id == old and len(resources.status()["packs"]) == 1
    assert not (tmp_path / "escape").exists()


def test_tamper_before_activation_and_history_after_catalog_change(tmp_path, resources):
    file, pin, h = pack(tmp_path)
    key = resources.install(file, pin)["packId"]
    image = resources.image(key, h)[0]
    original = image.read_bytes()
    image.write_bytes(b"changed")
    with pytest.raises(SyncError):
        resources.activate(key, None)
    assert resources.active_id is None
    image.write_bytes(original)
    # Historical display bytes remain data; never load old executable rules.
    future = DisplayResources(resources.home, "new-catalog", {})
    assert future.image(key, h)[0].read_bytes() == original
    with pytest.raises(SyncError):
        future.activate(key, None)


def test_capacity_rejection_retains_previous_version(tmp_path):
    store = DisplayResources(
        tmp_path.resolve() / "display",
        "catalog",
        {"p1": {"engineId": "effect"}},
        capacity=1,
    )
    file, pin, _ = pack(tmp_path)
    with pytest.raises(SyncError, match="存储已满"):
        store.install(file, pin)
    assert store.status()["packs"] == []
