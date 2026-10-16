import hashlib
import json
import sqlite3

import pytest

from packages.sync.common import read, write
from packages.sync.releases import Releases
from packages.sync.runtime import Worker
from scripts.battle import h2_manage


def invoke(monkeypatch, tmp_path, *options):
    monkeypatch.setattr('sys.argv', [
        'h2_manage.py', 'publish', '--db', str(tmp_path / 'user.sqlite'),
        '--home', str(tmp_path / 'sync'), *options,
    ])
    h2_manage.main()


def test_force_publish_checks_five_presets_and_only_rebinds_candidate(tmp_path, monkeypatch, capsys):
    database = tmp_path / 'user.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE probe(value TEXT)')
        db.execute("INSERT INTO probe VALUES('keep user data')")
    original_database = database.read_bytes()
    effects_path = h2_manage.ROOT / 'data/simulation/effects.json'
    original_effects = effects_path.read_bytes()
    original_engine = json.loads(original_effects)['engineVersion']
    monkeypatch.setattr(h2_manage.reg, 'select', lambda *args: pytest.fail('routing must use the frozen candidate'))

    invoke(monkeypatch, tmp_path, '--force')

    home = tmp_path / 'sync'
    publication = read(home / 'h2-publication.json')
    assert publication['forced'] is True
    assert publication['sourceEffectEngineVersion'] == original_engine
    assert len(publication['strategies']) == 5
    releases = Releases(home)
    assert releases.current() == publication['releaseId']
    manifest = releases.verify(publication['releaseId'])
    assert manifest['metadata']['forced'] is True
    assert manifest['metadata']['sourceEffectEngineVersion'] == original_engine
    archived_effects = read(releases.path(publication['releaseId']) / 'data/simulation/effects.json')
    assert archived_effects['engineVersion'] == manifest['engineVersion'] == publication['engineVersion']
    assert {k: v for k, v in archived_effects.items() if k != 'engineVersion'} == {
        k: v for k, v in json.loads(original_effects).items() if k != 'engineVersion'
    }
    assert effects_path.read_bytes() == original_effects
    assert database.read_bytes() == original_database
    backup = read(home / 'h2-publication.json')['backup']
    with sqlite3.connect(str(backup) + '/user.sqlite') as db:
        assert db.execute('SELECT value FROM probe').fetchone() == ('keep user data',)
    assert json.loads(capsys.readouterr().out)['releaseId'] == publication['releaseId']


@pytest.mark.parametrize('force', [False, True])
def test_failed_smoke_check_never_activates_even_with_force(tmp_path, monkeypatch, force):
    class RejectMatches(Worker):
        def request(self, method, path, *args, **kwargs):
            if path == '/api/battle/matches':
                return {
                    'status': 409,
                    'headers': {'content-type': 'application/json'},
                    'body': b'{"code":"EFFECT_ENGINE_MISMATCH","message":"incompatible assets"}',
                }
            return super().request(method, path, *args, **kwargs)

    monkeypatch.setattr('packages.sync.runtime.Worker', RejectMatches)
    home = tmp_path / 'sync'
    previous = '0' * 64
    write(home / 'current.json', {'releaseId': previous})
    source_effects = (h2_manage.ROOT / 'data/simulation/effects.json').read_bytes()

    with pytest.raises(RuntimeError, match='HTTP 409.*EFFECT_ENGINE_MISMATCH'):
        invoke(monkeypatch, tmp_path, *(['--force'] if force else []))

    assert Releases(home).current() == previous
    assert not (home / 'h2-publication.json').exists()
    assert read(home / 'h2-publish-failure.json')['error']['code'] == 'EFFECT_ENGINE_MISMATCH'
    candidate = Releases(home).list()[0]
    assert candidate['metadata'].get('forced', False) == force
    if not force:
        assert candidate['files']['data/simulation/effects.json'] == hashlib.sha256(source_effects).hexdigest()


def test_force_preserves_unbound_old_match_guard(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / 'user.sqlite') as db:
        db.execute('CREATE TABLE matches(id TEXT)')
        db.execute("INSERT INTO matches VALUES('old-match')")
    monkeypatch.setattr(Releases, 'archive', lambda *args, **kwargs: pytest.fail('old match guard bypassed'))
    with pytest.raises(SystemExit, match='Archive the old baseline'):
        invoke(monkeypatch, tmp_path, '--force')


@pytest.mark.parametrize('command', ['status', 'enable', 'rollback'])
def test_force_is_only_valid_for_publish(monkeypatch, command):
    monkeypatch.setattr('sys.argv', ['h2_manage.py', command, '--force'])
    with pytest.raises(SystemExit) as error:
        h2_manage.main()
    assert error.value.code == 2
