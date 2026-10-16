import json
from pathlib import Path
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.battle.presets import PRESETS
from packages.battle import h2
from packages.collection.domain import normalize, validation


def test_five_presets_are_complete_and_playable():
    assert len(PRESETS)==5
    assert len({p['id'] for p in PRESETS})==5
    for preset in PRESETS:
        assert sum(e['quantity'] for e in preset['entries'])==60
        assert validation(preset['entries'])['playable']


def test_preset_import_auth_reuse_and_default_creation(tmp_path):
    app = create_app(tmp_path/'presets.sqlite')
    client = TestClient(app)
    path = f"/api/battle/presets/{PRESETS[0]['id']}/revision"
    assert client.post(path,json={}).status_code == 401
    session = client.post('/api/battle/session',json={}).json()
    assert len(session['presets']) == 5
    for preset in PRESETS:
        path = f"/api/battle/presets/{preset['id']}/revision"
        rev = client.post(path,json={}).json()
        assert client.post(path,json={}).json()['id'] == rev['id']
        payload = {'requestId': preset['id'], 'revisionId': rev['id'], 'opponent':preset['id']}
        result = client.post('/api/battle/matches',json=payload)
        assert result.status_code == 201, result.text
        assert result.json()['aiVersion'] == h2.VERSION
        assert client.post('/api/battle/matches',json={**payload,'aiLevel':'H2'}).json()['matchId'] == result.json()['matchId']
    assert client.post('/api/battle/presets/missing/revision',json={}).status_code == 404
