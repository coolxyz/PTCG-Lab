"""Public native source/authority gate; does not claim platform acceptance."""

import json
from pathlib import Path
import tomllib


def check(root):
    root = Path(root)
    base = root / "apps/native-shell/src-tauri"
    errors = []
    try:
        config = json.loads((base / "tauri.conf.json").read_text())
        cargo = tomllib.loads((base / "Cargo.toml").read_text())
        lock = tomllib.loads((base / "Cargo.lock").read_text())
        runtime = json.loads((root / "scripts/native/runtime-lock.json").read_text())
        if (
            config["app"]["withGlobalTauri"] is not False
            or config["app"]["security"]["capabilities"] != []
        ):
            errors.append("native GUI gained command authority")
        if config["build"].get("windows", {}).get("staticVCRuntime") is not True:
            errors.append("native Windows CRT contract drift")
        if config["app"]["windows"] != [] or config["bundle"]["targets"] != ["app"]:
            errors.append("native proof platform/window scope drift")
        if (
            cargo["package"]["rust-version"] != "1.90"
            or cargo["package"]["publish"] is not False
        ):
            errors.append("native toolchain/publication drift")
        dependencies = dict(cargo["dependencies"])
        dependencies.update(cargo["build-dependencies"])
        for target in cargo.get("target", {}).values():
            dependencies.update(target.get("dependencies", {}))
        for name, value in dependencies.items():
            version = value if isinstance(value, str) else value["version"]
            if not version.startswith("=") or not any(
                p["name"] == name and p["version"] == version[1:]
                for p in lock["package"]
            ):
                errors.append("unlocked native dependency: " + name)
        windows = cargo["target"]["cfg(windows)"]["dependencies"]["windows-sys"]
        if (
            windows["version"] != "=0.61.2"
            or set(windows["features"])
            != {
                "Win32_Foundation",
                "Win32_Security",
                "Win32_Security_Authorization",
                "Win32_Storage_FileSystem",
                "Win32_System_IO",
                "Win32_System_Pipes",
                "Win32_System_Threading",
                "Win32_System_JobObjects",
                "Win32_System_Memory",
                "Win32_System_RemoteDesktop",
                "Win32_UI_WindowsAndMessaging",
            }
            or cargo["features"]
            != {"default": ["desktop"], "desktop": ["dep:tauri", "dep:tauri-build"]}
            or cargo["bin"][0]["required-features"] != ["desktop"]
            or cargo["dependencies"]["tauri"].get("optional") is not True
            or cargo["build-dependencies"]["tauri-build"].get("optional") is not True
        ):
            errors.append("native platform/features contract drift")
        if (
            cargo["dependencies"]["tauri"]["version"] != "=" + runtime["tauri"]
            or cargo["build-dependencies"]["tauri-build"]["version"]
            != "=" + runtime["tauriBuild"]
        ):
            errors.append("native runtime version contract drift")
        if list((base / "capabilities").glob("*.json")):
            errors.append("native proof gained capability files")
    except (OSError, KeyError, TypeError, ValueError):
        errors.append("missing or invalid native source contract")
    return errors
