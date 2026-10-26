import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_launcher_uses_project_cwd_and_loopback(monkeypatch, tmp_path, platform):
    launcher = load("launcher", "scripts/start.py")
    project = tmp_path / "中文 project space"
    interpreter = (
        project / ".venv" / ("Scripts/python.exe" if platform == "nt" else "bin/python")
    )
    for path in [
        interpreter,
        *(
            project / name
            for name in (
                "apps/web/dist/index.html",
                "runtime/engine/ptcg",
                "artifacts/engine/overlay-hashes.json",
                "data/catalog/catalog.json",
            )
        ),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name=platform, environ={}))
    calls = []
    monkeypatch.setattr(sys, "argv", ["start.py", "--port", "8799"])
    monkeypatch.setattr(
        launcher.subprocess,
        "call",
        lambda command, **kw: calls.append((command, kw)) or 0,
    )
    assert launcher.main() == 0
    command, options = calls[0]
    assert command[-4:] == ["--host", "127.0.0.1", "--port", "8799"]
    assert command[0] == str(interpreter)
    assert command[1:3] == ["-X", "utf8"]
    assert options["cwd"] == project
    assert options["env"]["PYTHONUTF8"] == "1"
    assert options["env"]["PYTHONPATH"] == str(project)


def test_release_verify_rejects_tampering_and_traversal(tmp_path):
    import hashlib
    import json
    import pytest

    release = load("release_tool", "scripts/release.py")
    (tmp_path / "file").write_bytes(b"original")
    manifest = tmp_path / "RELEASE-FILES.json"
    manifest.write_text(json.dumps({"file": hashlib.sha256(b"original").hexdigest()}))
    release.verify(tmp_path)
    (tmp_path / "file").write_bytes(b"changed")
    with pytest.raises(RuntimeError):
        release.verify(tmp_path)
    manifest.write_text(json.dumps({"../outside": "bad"}))
    with pytest.raises(RuntimeError):
        release.verify(tmp_path)


def test_integrated_launcher_uses_one_process_and_explicit_state(monkeypatch, tmp_path):
    launcher = load("integrated_launcher", "scripts/start.py")
    project = tmp_path / "product"
    for name in (
        ".venv/bin/python",
        "apps/web/dist/index.html",
        "runtime/engine/ptcg",
        "artifacts/engine/overlay-hashes.json",
        "data/catalog/catalog.json",
        "online/online.html",
        "online.json",
    ):
        p = project / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="posix", environ={}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "start.py",
            "--port",
            "18789",
            "--online-config",
            str(project / "online.json"),
            "--online-build",
            str(project / "online"),
            "--online-state",
            str(project / "private"),
            "--no-browser",
        ],
    )
    calls = []
    monkeypatch.setattr(
        launcher.subprocess, "call", lambda command, **kw: calls.append(command) or 0
    )
    assert launcher.main() == 0 and len(calls) == 1
    assert calls[0][3:5] == ["-m", "apps.api.product"]
    assert calls[0][calls[0].index("--state-dir") + 1] == str(project / "private")
    assert calls[0][-1] == "--no-browser"


def test_setup_installs_native_packages_and_builds_complete_product(
    monkeypatch, tmp_path
):
    setup = load("product_setup", "scripts/portable/setup.py")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.sys, "version_info", (3, 14))
    monkeypatch.setattr(setup.shutil, "which", lambda tool: tool)
    calls = []
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda command, **options: calls.append((command, options)),
    )
    setup.main()
    commands = [command for command, _ in calls]
    assert any(
        str(tmp_path / "apps/desktop-api/requirements.lock") in command
        for command in commands
    )
    packages = next(c for c in commands if "--no-deps" in c)
    assert all(
        str(tmp_path / p) in packages
        for p in [
            "packages/battle-protocol",
            "packages/engine-provider",
            "packages/online-client",
            "apps/desktop-api",
        ]
    )
    assert ["npm", "run", "product:build"] in commands
