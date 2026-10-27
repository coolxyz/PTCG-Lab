"""Native Keychain encryption, authenticated storage and explicit identity import."""

from copy import deepcopy
import json
import os
import subprocess
import sys
import uuid

import pytest
from ptcg_desktop_api.secure_storage import (
    MAGIC,
    SERVICE,
    KeychainCipher,
    native_vault,
    import_identity,
)
from ptcg_online_client.client import Client
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from ptcg_online_client.vault import Vault


class MemoryKeychain:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, password):
        self.values[(service, account)] = password


@pytest.fixture
def keychain():
    return MemoryKeychain()


@pytest.fixture
def server():
    return Server("https://native.example.test")


def test_native_vault_is_encrypted_and_reopens_same_journal(tmp_path, keychain, server):
    root = tmp_path / "private"
    first = native_vault(root, server, keychain=keychain)
    with first.transaction() as state:
        state["credentials"] = {"accessToken": "synthetic-sensitive-access"}
        state["pending"] = {"match": {"commandId": "stable-original-request"}}
        expected = deepcopy(state)
        first.save()
    ciphertext = first.path.read_bytes()
    assert ciphertext.startswith(MAGIC)
    assert b"synthetic-sensitive-access" not in ciphertext
    assert b"stable-original-request" not in ciphertext
    assert len(keychain.values) == 1
    first.close()
    second = native_vault(root, server, keychain=keychain)
    with second.transaction() as state:
        assert state == expected
        second.save()
    assert second.path.read_bytes() != ciphertext  # fresh nonce on every write
    second.close()


def test_ciphertext_tamper_missing_key_and_wrong_directory_fail_closed(
    tmp_path, keychain, server
):
    root = tmp_path / "private"
    vault = native_vault(root, server, keychain=keychain)
    path = vault.path
    vault.close()
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        native_vault(root, server, keychain=keychain)
    path.write_bytes(raw)
    with pytest.raises(ClientError, match="CREDENTIAL_KEY_UNAVAILABLE"):
        native_vault(root, server, keychain=MemoryKeychain())
    assert path.read_bytes() == raw
    wrong = KeychainCipher(tmp_path / "another", server, create=True, keychain=keychain)
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        wrong.decode(raw)


def test_keychain_failure_never_falls_back_to_plaintext(tmp_path, server):
    class Denied:
        def get_password(self, *args):
            raise RuntimeError("private-system-diagnostic")

    with pytest.raises(ClientError) as error:
        native_vault(tmp_path / "private", server, keychain=Denied())
    assert error.value.code == "CREDENTIAL_BACKEND_UNAVAILABLE"
    assert "private-system-diagnostic" not in str(error.value)
    assert not list((tmp_path / "private").glob("*.json"))


def test_explicit_import_preserves_identity_pending_and_source_bytes(
    tmp_path, keychain, server
):
    source, target = tmp_path / "old", tmp_path / "native"
    old = Vault(source, server)
    with old.transaction() as state:
        state.update(
            credentials={"refreshToken": "synthetic-refresh"},
            pending={"match": {"body": {"commandId": "same-request"}}},
        )
        expected = deepcopy(state)
        old.save()
    original = old.path.read_bytes()
    old.close()
    with pytest.raises(ClientError, match="NATIVE_IDENTITY_IMPORT_REQUIRED"):
        native_vault(source, server, keychain=keychain)
    assert not keychain.values
    import_identity(source, target, server, keychain=keychain)
    assert (source / (server.key + ".json")).read_bytes() == original
    restored = native_vault(target, server, keychain=keychain)
    with restored.transaction() as state:
        assert state == expected
    assert b"synthetic-refresh" not in restored.path.read_bytes()
    restored.close()
    with pytest.raises(ClientError, match="IDENTITY_DESTINATION_EXISTS"):
        import_identity(source, target, server, keychain=keychain)


def test_client_accepts_only_native_supplied_vault_factory(tmp_path, keychain, server):
    client = Client(
        server,
        tmp_path / "private",
        vault_factory=lambda r, s: native_vault(r, s, keychain=keychain),
    )
    assert client.status()["identity"] is None
    assert client.vault.path.read_bytes().startswith(MAGIC)
    client.close()


def test_operator_import_selects_configured_profile_and_refuses_running_app(
    tmp_path, monkeypatch, server
):
    from ptcg_desktop_api.identity import migrate
    from ptcg_desktop_api.native import StateLock

    state = (tmp_path / "app").resolve()
    state.mkdir(mode=0o700)
    (state / "connection.json").write_text(
        json.dumps({"servers": {"test": {"origin": server.origin}}})
    )
    calls = []
    monkeypatch.setattr(
        "ptcg_desktop_api.identity.import_identity", lambda *args: calls.append(args)
    )
    lock = StateLock(state)
    try:
        with pytest.raises(BlockingIOError):
            migrate(tmp_path / "source", state, "test")
        assert not calls
    finally:
        lock.close()
    migrate(tmp_path / "source", state, "test")
    assert len(calls) == 1 and calls[0][1] == state / "credentials"
    assert calls[0][2].key == server.key


@pytest.mark.skipif(
    os.environ.get("PTCG_NATIVE_KEYCHAIN_TEST") != "1" or sys.platform != "darwin",
    reason="Explicit macOS Keychain acceptance required",
)
def test_actual_macos_keychain_reopens_across_processes(tmp_path, server):
    # Only a newly generated synthetic test item is created/read/deleted.
    from keyring.backends.macOS import Keyring

    root = (tmp_path / ("p8a-" + uuid.uuid4().hex)).resolve()
    backend = Keyring()
    cipher = KeychainCipher(root, server, create=True, keychain=backend)
    account = cipher.account
    try:
        source = tmp_path / "legacy-identity"
        original = Vault(source, server)
        with original.transaction() as state:
            state["pending"] = {"synthetic": {"commandId": "exact-request"}}
            original.save()
        source_bytes = original.path.read_bytes()
        original.close()
        import_identity(source, root, server)
        assert (source / (server.key + ".json")).read_bytes() == source_bytes
        script = "from pathlib import Path; from ptcg_desktop_api.secure_storage import native_vault; from ptcg_online_client.transport import Server; import sys; v=native_vault(Path(sys.argv[1]),Server(sys.argv[2])); assert v.state['pending']=={'synthetic':{'commandId':'exact-request'}}; v.close(); print('keychain-reopen-passed')"
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(root), server.origin],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert (
            result.returncode == 0 and result.stdout.strip() == "keychain-reopen-passed"
        )
    finally:
        backend.delete_password(SERVICE, account)
