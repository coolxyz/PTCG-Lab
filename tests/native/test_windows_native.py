"""Opt-in tests for real DPAPI/NTFS/handle locks. WSL2 and mocks do not qualify."""

from copy import deepcopy
import os
import platform
import subprocess
import sys
import time

import pytest
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.transport import Server

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("PTCG_WINDOWS_NATIVE_TEST") != "1",
    reason="Native Windows x64 with explicit PTCG_WINDOWS_NATIVE_TEST=1 required",
)


@pytest.fixture
def server():
    assert os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64"}
    return Server("https://windows-native.example.test")


def test_real_windows_first_launch_accepts_only_private_webview_cache(tmp_path):
    import json
    from ptcg_desktop_api.installation import (
        CONTRACT,
        RECORD,
        InstallError,
        startup_guard,
    )
    from ptcg_desktop_api.windows_storage import WindowsDirectory

    payload = tmp_path / "synthetic-payload"
    payload.mkdir()
    (payload / "INSTALLATION.json").write_text(json.dumps(CONTRACT))
    (payload / "manifest.json").write_text('{"synthetic":"first-launch-contract-only"}')
    for existing_product in [False, True]:
        state = tmp_path / ("fresh" if not existing_product else "existing-product")
        owner = WindowsDirectory(state)
        owner.close()
        cache = WindowsDirectory(state / "webview")
        cache.close()
        (state / "webview/cache-fixture").write_bytes(b"synthetic browser cache")
        if existing_product:
            (state / "app.sqlite").write_bytes(b"retained product data")
            with pytest.raises(InstallError, match="DESKTOP_INSTALL_REQUIRED"):
                startup_guard(state, payload)
            assert not (state / RECORD).exists()
            assert (state / "app.sqlite").read_bytes() == b"retained product data"
        else:
            startup_guard(state, payload)
            startup_guard(state, payload)
            assert (state / RECORD).is_file()
        assert (state / "webview/cache-fixture").is_file()


def test_real_dpapi_encrypted_journal_reopens_in_another_process(tmp_path, server):
    from ptcg_desktop_api.windows_storage import MAGIC, windows_vault

    root = tmp_path / "中文 profile with spaces"
    first = windows_vault(root, server)
    try:
        with first.transaction() as state:
            state.update(
                credentials={"refreshToken": "synthetic-refresh"},
                pending={"match": {"commandId": "exact-request"}},
            )
            first.save()
            expected = deepcopy(state)
        encoded = first.path.read_bytes()
        assert encoded.startswith(MAGIC) and b"synthetic-refresh" not in encoded
        assert b"exact-request" not in encoded
    finally:
        first.close()
    code = "import sys; from ptcg_desktop_api.windows_storage import windows_vault; from ptcg_online_client.transport import Server; v=windows_vault(sys.argv[1],Server(sys.argv[2])); assert v.state['pending']['match']['commandId']=='exact-request'; v.close(); print('reopened')"
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(root), server.origin],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert child.returncode == 0 and child.stdout.strip() == "reopened"
    reopened = windows_vault(root, server)
    try:
        assert reopened.state == expected
    finally:
        reopened.close()


def test_real_handle_lock_excludes_a_second_process(tmp_path, server):
    from ptcg_desktop_api.windows_storage import windows_vault

    root = tmp_path / "exclusive"
    owner = windows_vault(root, server)
    try:
        code = "import sys; from ptcg_desktop_api.windows_storage import windows_vault; from ptcg_online_client.transport import Server; from ptcg_online_client.contracts import ClientError\ntry: windows_vault(sys.argv[1],Server(sys.argv[2]))\nexcept ClientError as e: print(e.code)\nelse: raise SystemExit('unexpected second owner')"
        second = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(root), server.origin],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert second.returncode == 0 and second.stdout.strip() == "PROFILE_IN_USE"
    finally:
        owner.close()


def test_real_dpapi_tamper_and_directory_move_preserve_original_bytes(tmp_path, server):
    from ptcg_desktop_api.windows_storage import WindowsFiles, windows_vault

    root = tmp_path / "original"
    original = windows_vault(root, server)
    path = original.path
    original.close()
    encoded = path.read_bytes()
    corrupted = encoded[:-1] + bytes([encoded[-1] ^ 1])
    path.write_bytes(corrupted)
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        windows_vault(root, server)
    assert path.read_bytes() == corrupted
    path.write_bytes(encoded)
    moved = tmp_path / "moved"
    store = WindowsFiles(moved, server.key)
    try:
        store.replace(encoded)
    finally:
        store.close()
    with pytest.raises(ClientError, match="INVALID_STORAGE"):
        windows_vault(moved, server)
    assert (moved / path.name).read_bytes() == encoded


def test_real_windows_acl_rejects_everyone_without_repair(tmp_path, server):
    import win32security as security
    from ptcg_desktop_api.windows_storage import windows_vault

    root = tmp_path / "bad-acl"
    owner = windows_vault(root, server)
    path = owner.path
    owner.close()
    original = path.read_bytes()
    sd = security.GetNamedSecurityInfo(
        str(root), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
    )
    dacl = sd.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(
        security.ACL_REVISION, 0x120089, security.ConvertStringSidToSid("S-1-1-0")
    )
    security.SetNamedSecurityInfo(
        str(root),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION
        | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        windows_vault(root, server)
    assert path.read_bytes() == original


def test_real_windows_hardlink_and_junction_are_rejected(tmp_path, server):
    from ptcg_desktop_api.windows_storage import windows_vault

    root = tmp_path / "hardlink"
    owner = windows_vault(root, server)
    path = owner.path
    owner.close()
    os.link(path, tmp_path / "linked-journal")
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        windows_vault(root, server)
    target = tmp_path / "junction target"
    target.mkdir()
    link = tmp_path / "junction"
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        windows_vault(link, server)


def test_real_windows_failed_replace_poison_owner_and_keep_journal(
    tmp_path, server, monkeypatch
):
    from ptcg_desktop_api.windows_storage import windows_vault

    vault = windows_vault(tmp_path / "write-failure", server)
    try:
        original = vault.path.read_bytes()

        def fail(*args):
            raise OSError("synthetic private diagnostic")

        monkeypatch.setattr(vault.storage.owner.file, "MoveFileEx", fail)
        with pytest.raises(ClientError, match="STORAGE_UNAVAILABLE"):
            with vault.transaction() as state:
                state["pending"] = {"match": {"commandId": "preserved-id"}}
                vault.save()
        assert vault.path.read_bytes() == original
        with pytest.raises(ClientError, match="STORAGE_UNAVAILABLE"):
            with vault.transaction():
                pass
    finally:
        vault.close()


def test_real_windows_killed_owner_releases_lock_and_preserves_pending(
    tmp_path, server
):
    from ptcg_desktop_api.windows_storage import windows_vault

    root, marker = tmp_path / "crashed", tmp_path / "ready"
    code = "import sys,time; from pathlib import Path; from ptcg_desktop_api.windows_storage import windows_vault; from ptcg_online_client.transport import Server; v=windows_vault(sys.argv[1],Server(sys.argv[2])); v.state['pending']={'match':{'commandId':'unchanged'}}; v.save(); Path(sys.argv[3]).write_text('ready'); time.sleep(60)"
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code, str(root), server.origin, str(marker)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            if marker.exists():
                break
            assert child.poll() is None
            time.sleep(0.1)
        assert marker.exists()
    finally:
        child.kill()
        child.wait(timeout=10)
    recovered = windows_vault(root, server)
    try:
        assert recovered.state["pending"] == {"match": {"commandId": "unchanged"}}
    finally:
        recovered.close()


def test_windows_host_uses_acl_checks_instead_of_posix_modes(tmp_path, server):
    from ptcg_desktop_api.native import StateLock
    from ptcg_desktop_api.bridge import Bridge
    from ptcg_desktop_api.host import Host
    from ptcg_desktop_api.resources import Resources

    root = tmp_path / "native profile"
    lock = StateLock(root)
    host = None
    try:
        with pytest.raises(ClientError, match="PROFILE_IN_USE"):
            StateLock(root)
        build = tmp_path / "gui"
        build.mkdir()
        (build / "online.html").write_text("<h1>synthetic</h1>")
        host = Host(
            Bridge({}, root / "credentials", allow_offline=True),
            "http://127.0.0.1:18786",
            build,
            Resources(root / "resources"),
            root / "launch",
        )
        assert len(host.native_session()) == 43
    finally:
        if host:
            host.close()
        lock.close()


def test_real_windows_identity_handoff_and_native_import_preserve_journal(
    tmp_path, server
):
    import shutil
    from ptcg_desktop_api.windows_storage import MAGIC, windows_vault
    from ptcg_desktop_api.identity_transfer import export_identity, import_bundle
    from ptcg_desktop_api.secure_storage import import_identity

    source = tmp_path / "source-native"
    owner = windows_vault(source, server)
    owner.state.update(
        credentials={"refreshToken": "synthetic-refresh"},
        pending={
            "match": {"commandId": "exact-request", "ruleReleaseId": "exact-current"}
        },
        refreshPending=True,
    )
    owner.save()
    expected = deepcopy(owner.state)
    raw = owner.path.read_bytes()
    owner.close()
    bundle = tmp_path / "private export" / "identity.ptcg"
    export_identity(
        source, bundle, server, "synthetic password 中文", source_kind="native"
    )
    public_copy = tmp_path / "copied-from-other-device.ptcg"
    shutil.copyfile(bundle, public_copy)
    target = tmp_path / "imported"
    import_bundle(public_copy, target, server, "synthetic password 中文")
    restored = windows_vault(target, server)
    try:
        assert restored.state == expected and restored.path.read_bytes().startswith(
            MAGIC
        )
        assert b"synthetic-refresh" not in restored.path.read_bytes()
    finally:
        restored.close()
    direct = tmp_path / "direct-native-import"
    import_identity(source, direct, server, source_kind="native")
    restored = windows_vault(direct, server)
    try:
        assert restored.state == expected and restored.path.read_bytes() != raw
    finally:
        restored.close()
    assert (source / (server.key + ".json")).read_bytes() == raw
    with pytest.raises(ClientError, match="IDENTITY_DESTINATION_EXISTS"):
        import_bundle(public_copy, target, server, "synthetic password 中文")


def test_real_windows_identity_refuses_running_target_and_unsafe_destination(
    tmp_path, server
):
    import win32security as security
    from ptcg_desktop_api.native import StateLock
    from ptcg_desktop_api.identity import migrate
    from ptcg_desktop_api.identity_transfer import export_identity, import_bundle
    from ptcg_desktop_api.windows_storage import windows_vault, WindowsDirectory

    state = tmp_path / "running-target"
    lock = StateLock(state)
    try:
        with pytest.raises(ClientError, match="PROFILE_IN_USE"):
            migrate(tmp_path / "source", state, "test", source_kind="native")
    finally:
        lock.close()
    source = tmp_path / "source"
    owner = windows_vault(source, server)
    owner.close()
    bundle = tmp_path / "export" / "bundle.ptcg"
    export_identity(source, bundle, server, "synthetic password", source_kind="native")
    target = tmp_path / "bad-acl"
    directory = WindowsDirectory(target)
    directory.close()
    sd = security.GetNamedSecurityInfo(
        str(target), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
    )
    dacl = sd.GetSecurityDescriptorDacl()
    dacl.AddAccessAllowedAce(
        security.ACL_REVISION, 0x120089, security.ConvertStringSidToSid("S-1-1-0")
    )
    security.SetNamedSecurityInfo(
        str(target),
        security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION
        | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        dacl,
        None,
    )
    with pytest.raises(ClientError, match="UNSAFE_STORAGE"):
        import_bundle(bundle, target, server, "synthetic password")
    assert not list(target.glob("*.json"))


def test_real_windows_unicode_and_long_private_path_reopens_exact_pending(
    tmp_path, server
):
    from ptcg_desktop_api.windows_storage import windows_vault

    root = tmp_path / ("中文 with spaces-" + "a" * 70) / ("b" * 90) / ("c" * 90)
    assert len(str(root)) > 260
    owner = windows_vault(root, server)
    owner.state["pending"] = {"match": {"commandId": "unchanged-long-path"}}
    owner.save()
    owner.close()
    reopened = windows_vault(root, server)
    try:
        assert reopened.state["pending"] == {
            "match": {"commandId": "unchanged-long-path"}
        }
    finally:
        reopened.close()
