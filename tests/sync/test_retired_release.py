"""Retired engine code is not needed for authenticated, read-only saved frames."""
import json
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import RAW
from packages.sync.common import write


def test_retired_match_is_read_only_and_replay_still_requires_owner(tmp_path):
    app = create_app(tmp_path / 'app.sqlite')
    with TestClient(app) as client:
        client.post('/api/battle/session', json={}).raise_for_status()
        deck = app.state.store.create_deck('retired', RAW['templates'][0]['entries'])
        revision = app.state.store.freeze(deck['id'], deck['version'])
        created = client.post('/api/battle/matches', json={'requestId':'retired-release-test','revisionId':revision['id'],'opponent':'dragapult'})
        assert created.status_code == 201, created.text
        mid = created.json()['matchId']
        old = 'a' * 64
        with app.state.battles.db(True) as db:
            record=json.loads(db.execute('SELECT body FROM matches WHERE id=?',(mid,)).fetchone()[0])
            record['config']['provenance']['syncReleaseId']=old
            db.execute('UPDATE matches SET engine_version=?,body=? WHERE id=?',('retired-engine',json.dumps(record),mid))
        write(app.state.sync.home/'retired.json',[old])
        write(app.state.sync.home/'baseline.json',{'releaseId':old})
        view=client.get('/api/battle/matches/'+mid)
        assert view.status_code==200 and view.json()['readOnly'] and view.json()['decision'] is None
        replay=client.get('/api/battle/matches/'+mid+'/replay')
        assert replay.status_code==200 and replay.json()['frames']
        mutation=client.post('/api/battle/matches/'+mid+'/resign',json={'expectedStateVersion':0})
        assert mutation.status_code==409 and mutation.json()['code']=='RELEASE_RETIRED'
        with TestClient(create_app(tmp_path/'app.sqlite')) as stranger:
            assert stranger.get('/api/battle/matches/'+mid+'/replay').status_code==401
            assert stranger.post('/api/battle/matches/'+mid+'/resign',json={'expectedStateVersion':0}).status_code==401
