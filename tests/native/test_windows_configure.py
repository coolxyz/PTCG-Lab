"""Connection setup verifies selected public inputs and refuses live/used state."""

from hashlib import sha256
import json

import pytest
from ptcg_desktop_api.configure import configure
from ptcg_desktop_api.native import StateLock
from ptcg_online_client.contracts import ClientError
from scripts.packaging.api_server import tls


@pytest.fixture
def kit(tmp_path):
    root = tmp_path / "server-kit"
    root.mkdir()
    tls(root, "localhost", "DNS:localhost")
    (root / "client.json").write_text(
        json.dumps({"origin": "https://localhost:8876", "caFile": "tls/ca.pem"})
    )
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "kind": "ptcg-api-compose-preview",
                "files": {
                    name: sha256((root / name).read_bytes()).hexdigest()
                    for name in ["client.json", "tls/ca.pem"]
                },
            }
        )
    )
    return root


def test_configuration_copies_only_public_ca_and_never_creates_identity(kit, tmp_path):
    state = tmp_path.resolve() / "state"
    configure(kit, state)
    config = (state / "connection.json").read_bytes()
    ca = (state / "preview-server-ca.pem").read_bytes()
    assert ca == (kit / "tls/ca.pem").read_bytes() and b"PRIVATE KEY" not in ca
    assert not (state / "credentials").exists()
    assert (
        json.loads(config)["servers"]["preview"]["origin"] == "https://localhost:8876"
    )
    with pytest.raises(ClientError, match="EXISTING_CONFIGURATION"):
        configure(kit, state)
    assert (state / "connection.json").read_bytes() == config and (
        state / "preview-server-ca.pem"
    ).read_bytes() == ca


def test_configuring_running_target_is_rejected(kit, tmp_path):
    state = tmp_path.resolve() / "running"
    lock = StateLock(state)
    try:
        with pytest.raises((ClientError, BlockingIOError)):
            configure(kit, state)
        assert not (state / "connection.json").exists()
    finally:
        lock.close()


def test_modified_server_kit_is_rejected_before_creating_state(kit, tmp_path):
    (kit / "client.json").write_text(
        json.dumps({"origin": "https://unexpected.example", "caFile": "tls/ca.pem"})
    )
    state = tmp_path.resolve() / "untouched"
    with pytest.raises(ClientError, match="INVALID_SERVER_KIT"):
        configure(kit, state)
    assert not state.exists()
