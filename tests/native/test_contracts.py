import json
from pathlib import Path
import shutil

import pytest
from scripts.native.contracts import check
from scripts.public.policy import source_files, allowed_source

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract(tmp_path):
    for name in [
        "apps/native-shell/src-tauri/tauri.conf.json",
        "apps/native-shell/src-tauri/Cargo.toml",
        "apps/native-shell/src-tauri/Cargo.lock",
        "scripts/native/runtime-lock.json",
    ]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return tmp_path


def test_native_source_contract_is_locked_and_exported(contract):
    assert check(contract) == []
    names = source_files(ROOT)
    for name in [
        "apps/native-shell/src-tauri/src/main.rs",
        "apps/native-shell/src-tauri/Cargo.lock",
        "packages/platform-services/src/index.ts",
    ]:
        assert name in names
    assert not allowed_source("apps/native-shell/src-tauri/target/release/backend")
    assert not allowed_source(
        "apps/native-shell/src-tauri/gen/schemas/desktop-schema.json"
    )


@pytest.mark.parametrize(
    "key,value", [("withGlobalTauri", True), ("capabilities", ["core:default"])]
)
def test_native_remote_gui_cannot_gain_implicit_authority(contract, key, value):
    path = contract / "apps/native-shell/src-tauri/tauri.conf.json"
    config = json.loads(path.read_text())
    target = config["app"] if key == "withGlobalTauri" else config["app"]["security"]
    target[key] = value
    path.write_text(json.dumps(config))
    assert check(contract)


def test_missing_lock_and_added_capability_file_are_rejected(contract):
    path = contract / "apps/native-shell/src-tauri/capabilities"
    path.mkdir()
    (path / "remote.json").write_text("{}")
    assert check(contract)
    (contract / "apps/native-shell/src-tauri/Cargo.lock").unlink()
    assert check(contract)


@pytest.mark.parametrize(
    "kind", ["windows-version", "windows-feature", "no-gui-default"]
)
def test_windows_platform_dependencies_and_gui_default_are_locked(contract, kind):
    path = contract / "apps/native-shell/src-tauri/Cargo.toml"
    source = path.read_text()
    if kind == "windows-version":
        source = source.replace('version = "=0.61.2"', 'version = "0.61"')
    elif kind == "windows-feature":
        source = source.replace('"Win32_System_JobObjects",', "")
    else:
        source = source.replace('default = ["desktop"]', "default = []")
    path.write_text(source)
    assert check(contract)


def test_windows_preview_cannot_accidentally_require_global_vc_runtime(contract):
    path = contract / "apps/native-shell/src-tauri/tauri.conf.json"
    config = json.loads(path.read_text())
    config["build"]["windows"]["staticVCRuntime"] = False
    path.write_text(json.dumps(config))
    assert "native Windows CRT contract drift" in check(contract)
