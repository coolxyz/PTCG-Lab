import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_uses_project_cwd_and_loopback(monkeypatch):
    launcher = load('launcher', 'scripts/start.py')
    calls = []
    monkeypatch.setattr(sys, 'argv', ['start.py', '--port', '8799'])
    monkeypatch.setattr(launcher.subprocess, 'call', lambda command, **kw: calls.append((command, kw)) or 0)
    assert launcher.main() == 0
    command, options = calls[0]
    assert command[-4:] == ['--host', '127.0.0.1', '--port', '8799']
    assert options['cwd'] == ROOT
    assert options['env']['PYTHONUTF8'] == '1'


def test_release_verify_rejects_tampering_and_traversal(tmp_path):
    import hashlib
    import json
    import pytest
    release = load('release_tool', 'scripts/release.py')
    (tmp_path / 'file').write_bytes(b'original')
    manifest = tmp_path / 'RELEASE-FILES.json'
    manifest.write_text(json.dumps({'file': hashlib.sha256(b'original').hexdigest()}))
    release.verify(tmp_path)
    (tmp_path / 'file').write_bytes(b'changed')
    with pytest.raises(RuntimeError):
        release.verify(tmp_path)
    manifest.write_text(json.dumps({'../outside': 'bad'}))
    with pytest.raises(RuntimeError):
        release.verify(tmp_path)
