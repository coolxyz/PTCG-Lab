import base64
from hashlib import sha256
import json
import pytest
from ptcg_desktop_api.resources import Resources
from ptcg_desktop_api.bridge import BridgeError

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jJgAAAABJRU5ErkJggg=="
)


def manifest(root, version="one", data=PNG, url=None):
    root.mkdir(exist_ok=True)
    (root / "files").mkdir(exist_ok=True)
    key = sha256(data).hexdigest()
    (root / "files" / key).write_bytes(data)
    file = root / (version + ".json")
    file.write_text(
        json.dumps(
            {
                "catalogVersion": "catalog",
                "assetVersion": version,
                "cards": [
                    {
                        "engineId": "demo-card",
                        "englishName": "Demo",
                        "cnName": "演示卡",
                        "image": {"sha256": key, "url": url},
                    }
                ],
            }
        )
    )
    return file, sha256(file.read_bytes()).hexdigest(), key


def test_hash_cache_offline_and_version_retention(tmp_path):
    file, pin, key = manifest(tmp_path / "source")
    cache = Resources(tmp_path / "cache", [(file, pin)])
    assert cache.image(key) == (PNG, "image/png")
    (file.parent / "files" / key).unlink()
    assert cache.image(key) == (PNG, "image/png")
    second, second_pin, key2 = manifest(tmp_path / "source", "two", PNG + b"new")
    cache.add(second, second_pin)
    assert cache.catalog("catalog", "one")["manifestSha256"] == pin
    assert cache.catalog("catalog", "two")["manifestSha256"] == second_pin
    assert cache.image(key2)[0] == PNG + b"new"
    assert cache.image(key)[0] == PNG
    assert cache.catalog("wrong", "two")["missing"]
    reopened = Resources(tmp_path / "cache", [(second, second_pin)])
    assert reopened.image(key)[0] == PNG


def test_invalid_manifest_image_hash_and_svg_are_rejected(tmp_path):
    file, pin, key = manifest(tmp_path / "source")
    with pytest.raises(ValueError):
        Resources(tmp_path / "bad", [(file, "0" * 64)])
    cache = Resources(tmp_path / "cache", [(file, pin)])
    (file.parent / "files" / key).write_bytes(b"wrong")
    with pytest.raises(BridgeError, match="INVALID_IMAGE"):
        cache.image(key)
    svg, pin2, svg_key = manifest(tmp_path / "source", "svg", b"<svg onload='x()'/>")
    cache.add(svg, pin2)
    with pytest.raises(BridgeError, match="INVALID_IMAGE"):
        cache.image(svg_key)
    with pytest.raises(BridgeError):
        cache.image("../../secret")


def test_download_has_no_bearer_redirects_or_unbounded_body(tmp_path, monkeypatch):
    file, pin, key = manifest(
        tmp_path / "source", url="https://images.example.test/demo.png"
    )
    (file.parent / "files" / key).unlink()
    calls = []

    class Connection:
        status = 200

        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def request(self, method, path, headers):
            assert headers == {"Accept-Encoding": "identity"}
            assert path == "/demo.png"

        def getresponse(self):
            return self

        def getheader(self, key, default):
            return default

        def read(self, limit):
            assert limit == 2 * 1024 * 1024 + 1
            return PNG

        def close(self):
            pass

    monkeypatch.setattr(
        "ptcg_desktop_api.resources.http.client.HTTPSConnection", Connection
    )
    cache = Resources(tmp_path / "cache", [(file, pin)])
    assert cache.image(key)[0] == PNG
    assert len(calls) == 1
    assert cache.image(key)[0] == PNG and len(calls) == 1
    (cache.root / key).unlink()
    Connection.status = 302
    with pytest.raises(BridgeError, match="IMAGE_UNAVAILABLE"):
        cache.image(key)
    assert len(calls) == 2


def test_cache_size_and_symlink_defenses(tmp_path):
    file, pin, key = manifest(tmp_path / "source")
    cache = Resources(tmp_path / "cache", [(file, pin)])
    (cache.root / key).symlink_to(file)
    with pytest.raises(BridgeError, match="INVALID_IMAGE"):
        cache.image(key)
    with pytest.raises(BridgeError, match="CACHE_CAPACITY"):
        Resources(tmp_path / "small", [(file, pin)], capacity=1)


def test_full_cache_reopens_without_counting_existing_manifest_twice(tmp_path):
    file, pin, key = manifest(tmp_path / "source")
    cache = Resources(tmp_path / "cache", [(file, pin)])
    cache.image(key)
    size = sum(p.stat().st_size for p in cache.root.iterdir())
    reopened = Resources(cache.root, [(file, pin)], capacity=size)
    assert reopened.image(key)[0] == PNG
