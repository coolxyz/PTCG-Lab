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
        if config["app"]["windows"] != [] or config["bundle"]["targets"] != ["app"]:
            errors.append("native proof platform/window scope drift")
        if (
            cargo["package"]["rust-version"] != "1.90"
            or cargo["package"]["publish"] is not False
        ):
            errors.append("native toolchain/publication drift")
        for name, value in cargo["dependencies"].items():
            version = value if isinstance(value, str) else value["version"]
            if not version.startswith("=") or not any(
                p["name"] == name and p["version"] == version[1:]
                for p in lock["package"]
            ):
                errors.append("unlocked native dependency: " + name)
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
