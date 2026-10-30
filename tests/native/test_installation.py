"""Offline installation fault matrix, with synthetic bundles and private profiles."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys
import subprocess

import pytest
from ptcg_desktop_api.installation import (
    InstallError,
    CONTRACT,
    RECORD,
    MARKER,
    startup_guard,
    payload_record,
    read,
)
from scripts.product.desktop_install import Installer, bundle, tree, write
from ptcg_desktop_api.native import StateLock
from ptcg_online_client.contracts import ClientError
from ptcg_online_client.vault import PosixFiles


def app(path, tag):
    payload = path / "Contents/Resources/backend"
    payload.mkdir(parents=True)
    (payload / "INSTALLATION.json").write_text(json.dumps(CONTRACT))
    (payload / "bootstrap.py").write_text(tag)
    (payload / "python").mkdir()
    (payload / "python/p").write_text("synthetic")
    (payload / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "platform": "macos-arm64",
                "files": {
                    p.relative_to(payload).as_posix(): sha256(
                        p.read_bytes()
                    ).hexdigest()
                    for p in payload.rglob("*")
                    if p.is_file()
                },
            }
        )
    )
    binary = path / "Contents/MacOS/ptcg-native-shell"
    binary.parent.mkdir()
    binary.write_text(tag)
    binary.chmod(0o700)
    return path


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Storage semantics are POSIX; target execution is not simulated by these
    # tests. Explicitly reject Windows below, and never claim a native GUI test.
    if os.name != "posix":
        pytest.skip("POSIX offline installer only")
    monkeypatch.setattr(sys, "platform", "darwin")
    root, state = tmp_path / "install", tmp_path / "profile"
    root.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    a, b = app(tmp_path / "A.app", "A"), app(tmp_path / "B.app", "B")
    return Installer(root, state), a, b


def install(i, a, **kw):
    return i.install(a, bundle(a)["bundleSha256"], **kw)


def seed(state):
    with sqlite3.connect(state / "app.sqlite") as db:
        db.executescript(
            "PRAGMA user_version=1; CREATE TABLE deck(id TEXT PRIMARY KEY, body TEXT); INSERT INTO deck VALUES('unchanged-id','my deck');"
        )
    (state / "credentials").mkdir(mode=0o700)
    (state / "credentials/ciphertext.json").write_bytes(b"encrypted-identity-journal")
    (state / "images").mkdir(mode=0o700)
    (state / "images/history.png").write_bytes(b"private-display-data")


def test_contract_matches_bundler():
    root = Path(__file__).resolve().parents[2]
    assert (
        json.loads((root / "scripts/native/installation-contract.json").read_text())
        == CONTRACT
    )


def test_first_launch_same_version_and_wrong_version_guard(setup):
    i, a, b = setup
    startup_guard(i.state, a / "Contents/Resources/backend")
    startup_guard(i.state, a / "Contents/Resources/backend")
    with pytest.raises(InstallError, match="DESKTOP_INSTALL_REQUIRED"):
        startup_guard(i.state, b / "Contents/Resources/backend")


def test_isolated_shell_acceptance_marker_is_not_user_data(setup):
    i, a, _ = setup
    (i.state / ".ptcg-acceptance.json").write_text("{}")
    startup_guard(i.state, a / "Contents/Resources/backend")
    assert (i.state / RECORD).is_file()


def test_windows_first_launch_cache_does_not_require_product_migration(
    setup, monkeypatch
):
    from ptcg_desktop_api import installation as module

    i, a, _ = setup
    cache = i.state / "webview"
    cache.mkdir(mode=0o700)
    (cache / "runtime-cache").write_bytes(b"synthetic browser cache")
    helper = module.browser_cache_entries
    assert helper(i.state, platform="darwin") == set()
    assert helper(i.state, platform="win32") == {"webview"}
    monkeypatch.setattr(
        module, "browser_cache_entries", lambda state: helper(state, platform="win32")
    )
    startup_guard(i.state, a / "Contents/Resources/backend")
    assert (i.state / RECORD).is_file() and (cache / "runtime-cache").is_file()


@pytest.mark.parametrize(
    "kind", ["database", "credentials", "file", "link", "permissions"]
)
def test_windows_cache_exception_keeps_real_data_and_unsafe_paths_blocked(
    setup, monkeypatch, kind
):
    from ptcg_desktop_api import installation as module

    i, a, _ = setup
    cache = i.state / "webview"
    if kind == "file":
        cache.write_text("not a directory")
    elif kind == "link":
        cache.symlink_to(a, target_is_directory=True)
    else:
        cache.mkdir(mode=0o755 if kind == "permissions" else 0o700)
        if kind == "database":
            (i.state / "app.sqlite").write_bytes(b"retained product")
        if kind == "credentials":
            (i.state / "credentials").mkdir(mode=0o700)
    helper = module.browser_cache_entries
    monkeypatch.setattr(
        module, "browser_cache_entries", lambda state: helper(state, platform="win32")
    )
    with pytest.raises(
        (InstallError, ValueError), match="INSTALL_UNSAFE_PATH|DESKTOP_INSTALL_REQUIRED"
    ):
        startup_guard(i.state, a / "Contents/Resources/backend")
    assert not (i.state / RECORD).exists()


def test_legacy_data_needs_explicit_adoption_and_keeps_exact_backup(setup):
    i, a, _ = setup
    seed(i.state)
    before = tree(i.state, profile=True)
    with pytest.raises(InstallError, match="DESKTOP_INSTALL_REQUIRED"):
        startup_guard(i.state, a / "Contents/Resources/backend")
    with pytest.raises(InstallError, match="EXPLICIT_ADOPTION"):
        install(i, a)
    result = install(i, a, adopt=True)
    tx, journal = i.journal(result["transaction"])
    assert tree(tx / "before") == before == journal["beforeFiles"]
    assert read(i.state / RECORD) == payload_record(a / "Contents/Resources/backend")
    assert (
        i.state / "credentials/ciphertext.json"
    ).read_bytes() == b"encrypted-identity-journal"


def test_upgrade_and_explicit_paired_rollback_preserve_ids_and_pre_rollback_state(
    setup,
):
    i, a, b = setup
    first = install(i, a)
    seed(i.state)
    before = tree(i.state, profile=True)
    changed = install(i, b)
    startup_guard(i.state, b / "Contents/Resources/backend")
    with pytest.raises(InstallError, match="DESKTOP_INSTALL_REQUIRED"):
        startup_guard(i.state, a / "Contents/Resources/backend")
    (i.state / "new-file").write_bytes(b"post-upgrade-data")
    after = tree(i.state, profile=True)
    rollback = i.rollback(changed["transaction"], expected=bundle(b)["bundleSha256"])
    assert tree(i.state, profile=True) == before
    assert tree(i.journal(rollback["transaction"])[0] / "before") == after
    assert i.active()["bundleSha256"] == first["bundleSha256"]
    startup_guard(i.state, a / "Contents/Resources/backend")


@pytest.mark.parametrize("point", ["prepared", "profile-written", "pointer-written"])
@pytest.mark.parametrize("mode", ["finish", "abort"])
def test_interruption_blocks_start_and_explicit_recovery_has_one_coherent_pair(
    setup, point, mode
):
    i, a, b = setup
    install(i, a)
    seed(i.state)
    before = tree(i.state, profile=True)

    def crash(stage):
        if stage == point:
            raise RuntimeError("simulated process loss")

    with pytest.raises(RuntimeError):
        install(i, b, fault=crash)
    with pytest.raises(InstallError, match="DESKTOP_RECOVERY_REQUIRED"):
        startup_guard(i.state, b / "Contents/Resources/backend")
    i.recover(mode)
    target = b if mode == "finish" else a
    startup_guard(i.state, target / "Contents/Resources/backend")
    assert i.active()["bundleSha256"] == bundle(target)["bundleSha256"]
    assert not (i.state / MARKER).exists()
    if mode == "abort":
        assert tree(i.state, profile=True) == before


@pytest.mark.parametrize("kind", ["native", "shell", "installer"])
def test_active_owner_prevents_every_profile_change(setup, kind):
    i, a, b = setup
    install(i, a)
    before = tree(i.state, profile=True)
    lock = (
        StateLock(i.state)
        if kind == "native"
        else PosixFiles(i.state if kind == "shell" else i.root, kind)
    )
    try:
        with pytest.raises((ClientError, BlockingIOError)):
            install(i, b)
    finally:
        lock.close()
    assert tree(i.state, profile=True) == before


@pytest.mark.parametrize(
    "kind",
    [
        "digest",
        "payload",
        "symlink",
        "hardlink",
        "futureSchema",
        "dbCorrupt",
        "disk",
        "snapshot",
    ],
)
def test_invalid_candidate_profile_or_backup_cannot_switch(setup, monkeypatch, kind):
    i, a, b = setup
    install(i, a)
    expected = bundle(b)["bundleSha256"]
    if kind == "digest":
        expected = "0" * 64
    elif kind == "payload":
        (b / "Contents/Resources/backend/bootstrap.py").write_text("changed")
    elif kind == "symlink":
        (i.state / "escape").symlink_to(a)
    elif kind == "hardlink":
        os.link(a / "Contents/MacOS/ptcg-native-shell", i.state / "linked")
    elif kind == "futureSchema":
        with sqlite3.connect(i.state / "app.sqlite") as db:
            db.execute("PRAGMA user_version=99")
    elif kind == "dbCorrupt":
        (i.state / "app.sqlite").write_bytes(b"not-sqlite")
    elif kind == "disk":
        from collections import namedtuple

        monkeypatch.setattr("shutil.disk_usage", lambda _: namedtuple("D", "free")(0))
    else:
        upgrade = install(i, b)
        tx, j = i.journal(upgrade["transaction"])
        (tx / "before" / RECORD).write_text("{}")
        with pytest.raises(InstallError, match="SNAPSHOT_CHANGED"):
            i.rollback(upgrade["transaction"], expected=expected)
        return
    before = i.active()
    with pytest.raises((InstallError, sqlite3.DatabaseError)):
        i.install(b, expected)
    assert i.active() == before and not (i.state / MARKER).exists()


def test_unsupported_windows_install_is_explicitly_pending(setup, monkeypatch):
    i, a, _ = setup
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(InstallError, match="INSTALL_PLATFORM_PENDING"):
        install(i, a)


def test_profile_path_binding_and_stale_rollback(setup):
    i, a, b = setup
    first = install(i, a)
    with pytest.raises(InstallError, match="ROLLBACK_PAIR_REQUIRED"):
        i.rollback(first["transaction"], expected=first["bundleSha256"])
    update = install(i, b)
    with pytest.raises(InstallError, match="STALE_ACTIVE"):
        i.rollback(update["transaction"], expected=first["bundleSha256"])
    other = Installer(i.root, i.state.parent / "different-profile")
    with pytest.raises(InstallError, match="PROFILE_MISMATCH"):
        install(other, a)


def test_corrupt_recovery_journal_keeps_startup_blocked(setup):
    i, a, b = setup
    install(i, a)

    def crash(stage):
        raise RuntimeError()

    with pytest.raises(RuntimeError):
        install(i, b, fault=crash)
    m = read(i.state / MARKER)
    tx, j = i.journal(m["transaction"])
    write(tx / "transaction.json", {**j, "extra": "tampered"})
    with pytest.raises(InstallError, match="SNAPSHOT_CHANGED"):
        i.recover("finish")
    assert (i.state / MARKER).is_file()


def test_paired_restore_handles_empty_directory_and_file_conflicts(setup):
    i, a, b = setup
    install(i, a)
    (i.state / "local-data").write_bytes(b"before")
    upgraded = install(i, b)
    (i.state / "local-data").unlink()
    (i.state / "local-data").mkdir()
    (i.state / "local-data" / "new").write_bytes(b"after")
    i.rollback(upgraded["transaction"], expected=bundle(b)["bundleSha256"])
    assert (i.state / "local-data").read_bytes() == b"before"


def test_future_profile_record_blocks_before_product_database_startup(setup):
    i, a, _ = setup
    write(i.state / RECORD, {"profileSchema": 99})
    with pytest.raises(InstallError, match="DESKTOP_SCHEMA_UNSUPPORTED"):
        startup_guard(i.state, a / "Contents/Resources/backend")
    assert not (i.state / "app.sqlite").exists()


def test_deep_existing_product_directories_are_preserved_inside_private_boundary(setup):
    i, a, b = setup
    install(i, a)
    deep = i.state / "app.sync/releases/version/data/catalog"
    deep.mkdir(parents=True, mode=0o755)
    (deep / "catalog.json").write_text('{"data":"unchanged"}')
    (i.state / "app.sync/info.json").write_text("{}")
    original_mode = deep.stat().st_mode
    before = tree(i.state, profile=True)
    update = install(i, b)
    assert deep.stat().st_mode == original_mode
    i.rollback(update["transaction"], expected=bundle(b)["bundleSha256"])
    assert tree(i.state, profile=True) == before


def test_surviving_owned_writer_holds_profile_until_it_exits(setup, monkeypatch):
    from packages.sync.runtime import native_writer_options

    i, a, b = setup
    install(i, a)
    owner = StateLock(i.state)
    monkeypatch.setenv("PTCG_NATIVE_LOCK_FD", str(owner.fd))
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            'import sys;sys.stdout.write("ready\\n");sys.stdout.flush();sys.stdin.read()',
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        **native_writer_options(),
    )
    try:
        assert child.stdout.readline() == b"ready\n"
        owner.close()
        with pytest.raises(BlockingIOError):
            install(i, b)
    finally:
        child.stdin.close()
        child.wait(timeout=10)
        child.stdout.close()
        owner.close()
    install(i, b)
