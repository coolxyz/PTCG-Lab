from hashlib import sha256
import json
import pytest
from scripts.migration.p5d_verify_bundle import verify
from scripts.migration.p5d_client_bundle import assemble


def test_kit_detects_corruption_and_rejects_path_escape(tmp_path):
    file = tmp_path / "web.js"
    file.write_bytes(b"approved build")
    manifest = tmp_path / "KIT-MANIFEST.json"
    data = {
        "schema": 1,
        "stage": "P5-D",
        "files": {"web.js": sha256(file.read_bytes()).hexdigest()},
    }
    manifest.write_text(json.dumps(data))
    assert verify(tmp_path)["status"] == "verified"
    file.write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path)
    data["files"] = {"../web.js": "0" * 64}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="path"):
        verify(tmp_path)


def test_kit_requires_explicit_built_assets_and_never_creates_partial_output(tmp_path):
    output = tmp_path / "kit"
    with pytest.raises(ValueError, match="Missing isolated web assets"):
        assemble(tmp_path / "packages", tmp_path / "web", output)
    assert not output.exists()


def test_kit_verification_rejects_linked_inputs(tmp_path):
    secret = tmp_path / "private"
    secret.write_bytes(b"not part of kit")
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "input").symlink_to(secret)
    (kit / "KIT-MANIFEST.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "stage": "P5-D",
                "files": {"input": sha256(secret.read_bytes()).hexdigest()},
            }
        )
    )
    with pytest.raises(ValueError, match="Linked"):
        verify(kit)
