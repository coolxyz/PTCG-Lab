"""Encrypted operator handoff preserves exact current journal and trust identity."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server
from ptcg_online_client.vault import Vault
from ptcg_desktop_api.identity_transfer import (
    export_identity,
    import_bundle,
    seal,
)
from ptcg_desktop_api.secure_storage import native_vault, import_identity
from ptcg_desktop_api.sealed_file import access
from tests.native.test_secure_storage import MemoryKeychain


@pytest.fixture
def identity(tmp_path):
    server = Server("https://transfer.example.test")
    source = tmp_path.resolve() / "source"
    owner = Vault(source, server)
    state = deepcopy(owner.state)
    state.update(
        credentials={"refreshToken": "synthetic-refresh"},
        pending={
            "match": {
                "commandId": "unchanged-id",
                "ruleReleaseId": "exact-current-rules",
            }
        },
        refreshPending=True,
    )
    state["matchHistory"] = {"match": {"sequence": 17}}
    owner.state = state
    owner.save()
    raw = owner.path.read_bytes()
    owner.close()
    return source, server, state, raw


def test_handoff_preserves_source_pending_refresh_and_history(identity, tmp_path):
    source, server, expected, original = identity
    bundle = tmp_path.resolve() / "private export" / "identity.ptcg"
    password = "synthetic passphrase 中文"
    export_identity(source, bundle, server, password)
    encrypted = bundle.read_bytes()
    assert b"synthetic-refresh" not in encrypted and b"unchanged-id" not in encrypted
    assert bundle.stat().st_mode & 0o077 == 0
    target = tmp_path.resolve() / "native"
    keychain = MemoryKeychain()
    import_bundle(bundle, target, server, password, keychain=keychain)
    owner = native_vault(target, server, keychain=keychain)
    try:
        assert owner.state == expected
        assert b"synthetic-refresh" not in owner.path.read_bytes()
    finally:
        owner.close()
    assert (source / (server.key + ".json")).read_bytes() == original
    assert bundle.read_bytes() == encrypted
    with pytest.raises(ClientError, match="IDENTITY_DESTINATION_EXISTS"):
        import_bundle(bundle, target, server, password, keychain=keychain)


@pytest.mark.parametrize(
    "kind", ["password", "server", "tamper", "truncated", "too-large"]
)
def test_invalid_handoff_never_creates_destination(identity, tmp_path, kind):
    source, server, state, _ = identity
    password = "synthetic passphrase"
    raw = seal(state, server, password)
    selected = server
    if kind == "password":
        password = "wrong passphrase"
    elif kind == "server":
        selected = Server("https://another.example.test")
    elif kind == "tamper":
        raw = raw[:-1] + bytes([raw[-1] ^ 1])
    elif kind == "truncated":
        raw = raw[:20]
    else:
        raw += b"x" * (9 * 1024 * 1024)
    bundle = tmp_path.resolve() / "transport" / "copy.ptcg"
    access(bundle, data=raw, limit=10 * 1024 * 1024)
    bundle.chmod(0o644)  # Transport ciphertext may have ordinary copy permissions.
    with pytest.raises(ClientError, match="INVALID_IDENTITY_BUNDLE|UNSAFE_STORAGE"):
        import_bundle(
            bundle,
            tmp_path / "destination",
            selected,
            password,
            keychain=MemoryKeychain(),
        )
    assert not (tmp_path / "destination").exists()


def test_atomic_import_guards_prevent_reset_or_overwrite(identity, tmp_path):
    source, server, state, original = identity
    with pytest.raises(ClientError, match="IDENTITY_SOURCE_MISSING"):
        Vault(tmp_path / "missing", server, require_existing=True)
    assert not list((tmp_path / "missing").glob("*.json"))
    with pytest.raises(ClientError, match="IDENTITY_DESTINATION_EXISTS"):
        Vault(source, server, initial_state=state, require_new=True)
    assert (source / (server.key + ".json")).read_bytes() == original
    owner = Vault(source, server)
    try:
        with pytest.raises(ClientError, match="PROFILE_IN_USE"):
            export_identity(
                source,
                tmp_path.resolve() / "export" / "out",
                server,
                "synthetic password",
            )
    finally:
        owner.close()


def test_native_to_native_import_reencrypts_for_destination(identity, tmp_path):
    source, server, expected, _ = identity
    keychain = MemoryKeychain()
    first, second = tmp_path / "first-native", tmp_path / "second-native"
    import_identity(source, first, server, keychain=keychain)
    original = (first / (server.key + ".json")).read_bytes()
    import_identity(first, second, server, keychain=keychain, source_kind="native")
    assert (first / (server.key + ".json")).read_bytes() == original
    restored = native_vault(second, server, keychain=keychain)
    try:
        assert restored.state == expected and restored.path.read_bytes() != original
    finally:
        restored.close()


def test_private_handoff_files_reject_links_loose_modes_and_overwrite(tmp_path):
    root = tmp_path.resolve() / "private"
    root.mkdir(mode=0o700)
    file = root / "file"
    access(file, data=b"encrypted")
    with pytest.raises(ClientError, match="PRIVATE_FILE_UNAVAILABLE"):
        access(file, data=b"other")
    link = root / "link"
    link.symlink_to(file)
    with pytest.raises(ClientError):
        access(link)
    hard = root / "hard"
    hard.hardlink_to(file)
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        access(file)
    hard.unlink()
    file.chmod(0o644)
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        access(file)


def test_cli_handoff_refuses_redirected_password_and_has_no_secret_flags(
    monkeypatch, capsys
):
    from ptcg_desktop_api.identity import transfer_main
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not-a-secret-channel"))
    assert (
        transfer_main(
            [
                "import",
                "--bundle",
                "synthetic",
                "--state-dir",
                str(Path.cwd() / "unused"),
                "--profile",
                "test",
            ]
        )
        == 1
    )
    assert (
        json.loads(capsys.readouterr().out)["code"] == "IDENTITY_INTERACTIVE_REQUIRED"
    )


def test_password_policy_and_https_binding_are_explicit(identity):
    _, server, state, _ = identity
    with pytest.raises(ClientError, match="IDENTITY_PASSWORD_LENGTH"):
        seal(state, server, "short")
    http = Server("http://127.0.0.1:12345", allow_loopback_http=True)
    with pytest.raises(ClientError, match="INVALID_SERVER"):
        seal(state, http, "synthetic password")


def test_transfer_cannot_silently_replace_server_ca_trust(identity, tmp_path):
    from scripts.packaging.api_server import tls

    _, _, state, _ = identity
    root = tmp_path / "ca"
    root.mkdir()
    tls(root, "localhost", "DNS:localhost")
    selected = Server("https://transfer.example.test", ca_file=str(root / "tls/ca.pem"))
    state = dict(state, profile=selected.key)
    raw = seal(state, selected, "synthetic password")
    from ptcg_desktop_api.identity_transfer import unseal

    with pytest.raises(ClientError, match="INVALID_IDENTITY_BUNDLE"):
        unseal(raw, Server(selected.origin), "synthetic password")


@pytest.mark.parametrize("kind", ["browser", "native"])
def test_operator_source_guard_refuses_an_active_product_owner(identity, kind):
    from ptcg_desktop_api.identity import source_guard
    from ptcg_desktop_api.native import StateLock
    from ptcg_online_client.vault import PosixFiles

    source, server, _, original = identity
    guard = (
        StateLock(source.parent)
        if kind == "native"
        else PosixFiles(source.parent, "product")
    )
    try:
        with pytest.raises((ClientError, BlockingIOError)):
            source_guard(source, kind)
        assert (source / (server.key + ".json")).read_bytes() == original
    finally:
        guard.close()
