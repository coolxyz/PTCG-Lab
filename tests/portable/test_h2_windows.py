"""Real Git CRLF checkout and UTF-8 worker contracts, runnable on either OS."""
import json
import shutil
import subprocess
from pathlib import Path

from apps.api.expert_policies import registry
from packages.sync.releases import Releases
from packages.sync.runtime import Worker

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {
    'h2-marnie-grimmsnarl': 'h2-grimmsnarl-pre-pivot-v22',
    'h2-gardevoir-ex': 'h2-gardevoir-basic-v5',
    'h2-dragapult-dusknoir': 'h2-dragapult-draw-development-v9',
    'h2-n-zoroark-ex': 'h2-zoroark-copy-chain-v3',
    'h2-joltik-box': 'h2-joltik-basic-v1',
}


def test_windows_checkout_preserves_shipped_bundle(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    checkout = tmp_path / 'Windows 中文 path'
    source.mkdir()
    checkout.mkdir()
    names = ['.gitattributes', 'apps/api/expert_service.py', 'apps/api/h2-rollout.json']
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / 'apps/api/expert_policies').iterdir()
              if p.suffix in ('.py', '.json')]
    for name in names:
        dest = source / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    (source / 'unprotected.txt').write_bytes(b'Windows checkout control\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'add', '--all'], cwd=source,
                   check=True, capture_output=True)
    subprocess.run(['git', '-c', 'core.autocrlf=true', 'checkout-index', '--all',
                    '--prefix=' + checkout.as_posix() + '/'], cwd=source,
                   check=True, capture_output=True)
    # The current repository pins all text to LF, including ordinary files,
    # even when a Windows checkout requests core.autocrlf=true.
    assert (checkout / 'unprotected.txt').read_bytes().endswith(b'\n')
    assert b'\r' not in (checkout / 'unprotected.txt').read_bytes()
    for name in names:
        if name.startswith('apps/api/expert_'):
            assert (checkout / name).read_bytes() == (ROOT / name).read_bytes(), name
    monkeypatch.setattr(registry, '__file__', str(checkout / 'apps/api/expert_policies/registry.py'))
    assert registry.bundle_hash() == json.loads((checkout / 'apps/api/h2-rollout.json').read_text(encoding='utf-8'))['bundleHash']


def test_frozen_worker_supports_unicode_and_spaces(tmp_path):
    home = tmp_path / '发布 目录' / 'sync'
    releases = Releases(home)
    release = releases.archive(metadata={'kind': 'windows-portability'})
    worker = Worker(releases, release, tmp_path / '我的 对战.sqlite')
    cookie = ''

    def call(path, body=None):
        nonlocal cookie
        response = worker.request('POST', path, {'cookie': cookie, 'content-type': 'application/json'},
                                  json.dumps(body or {}, ensure_ascii=False).encode('utf-8'))
        assert response['status'] in (200, 201), response['body'].decode('utf-8')
        if 'set-cookie' in response['headers']:
            cookie = response['headers']['set-cookie'].split(';')[0]
        return json.loads(response['body'])

    try:
        session = call('/api/battle/session')
        deck = call('/api/decks', {'name': '中文 卡组', 'entries': []})
        assert deck['name'] == '中文 卡组'
        assert len(session['presets']) == 5
        for preset in session['presets']:
            revision = call(f'/api/battle/presets/{preset["id"]}/revision')
            match = call('/api/battle/matches', {'requestId': 'unicode-' + preset['id'],
                         'revisionId': revision['id'], 'opponent': preset['id']})
            assert match['aiStrategy']['version'] == VERSIONS[preset['id']]
            assert match['observation']['opponent']['hand'] is None
            advanced = call(f'/api/battle/matches/{match["matchId"]}/advance')
            assert advanced['aiStrategy']['version'] == VERSIONS[preset['id']]
    finally:
        worker.close()
