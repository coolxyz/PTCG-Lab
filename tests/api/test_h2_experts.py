import copy,json,uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from apps.api.expert_policies import registry as reg
from packages.battle import h2
from packages.battle.presets import PRESETS

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr('packages.battle.service.secrets.randbits',lambda n:0)
    db=tmp_path/'test.sqlite';app=create_app(db);c=TestClient(app);c.post('/api/battle/session',json={})
    reg.rollout_path(db).write_text(json.dumps({'bundleHash':reg.bundle_hash(),'enabled':list(reg.POLICIES)}))
    rev=c.post('/api/battle/presets/'+PRESETS[0]['id']+'/revision',json={}).json()
    return c,app,rev,db

def create(s,preset,**kwargs):
    c,app,rev,db=s
    req={'requestId':str(uuid.uuid4()),'revisionId':rev['id'],'opponent':preset,'aiLevel':'H2',**kwargs}
    response=c.post('/api/battle/matches',json=req);assert response.status_code==201,response.text
    return response.json(),req

def test_five_routes_revision_idempotence_restore_rollback(setup):
    c,app,rev,db=setup
    for preset in PRESETS:
        v,req=create(setup,preset['id']);expected=preset['id'] if preset['id'] in reg.POLICIES else 'original-h2'
        assert v['aiStrategy']['id']==expected
        assert c.post('/api/battle/matches',json=req).json()['matchId']==v['matchId']
        advanced=c.post(f"/api/battle/matches/{v['matchId']}/advance").json();assert advanced.get('stateVersion')==1, advanced
        app.state.battles.cache.clear()
        assert c.get(f"/api/battle/matches/{v['matchId']}").json()['aiStrategy']['id']==expected
    expert=PRESETS[2];custom=app.state.store.create_deck('exact custom',expert['entries']);rv=app.state.store.freeze(custom['id'],custom['version'])
    v,req=create(setup,None,opponentRevisionId=rv['id']);assert v['aiStrategy']['id']==expert['id']
    reg.rollout_path(db).write_text(json.dumps({'bundleHash':reg.bundle_hash(),'enabled':[]}))
    new,_=create(setup,expert['id']);assert new['aiStrategy']['id']=='original-h2'
    assert c.post('/api/battle/matches',json=req).json()['aiStrategy']['id']==expert['id']
    # Already pinned match remains the same after disabling new-match routing.
    assert c.post(f"/api/battle/matches/{v['matchId']}/advance").json()['aiStrategy']['id']==expert['id']

def test_non_exact_and_a1_are_not_routed(setup):
    c,app,rev,db=setup
    entries=copy.deepcopy(PRESETS[2]['entries']);entries[0]['quantity']-=1;entries[1]['quantity']+=1
    assert reg.select(entries,db) is None
    for level in ('A1',):
        v,_=create(setup,PRESETS[0]['id'],aiLevel=level);assert 'aiStrategy' not in v

def test_fallback_redacts_private_diagnostics(setup,monkeypatch):
    from packages.battle.service import runtime
    c,app,rev,db=setup
    v,_=create(setup,PRESETS[0]['id'])
    service=app.state.battles;owner=service.owner(c.cookies.get('ptcg_practice_session'))
    with service.db() as connection:
        game=service._game(service._row(connection,owner,v['matchId']))
        dto=runtime().view(game,game.actor)
    pin=reg.select(PRESETS[0]['entries'],db);module=reg.POLICIES[pin['id']]
    def fail(*args,**kwargs):raise RuntimeError('SECRET HAND')
    monkeypatch.setattr(module,'decide',fail)
    cmd,detail=reg.decide(dto,pin);assert detail['fallback'] and 'SECRET' not in json.dumps(detail)
    assert set(detail)=={'fallback','fallbackReason','aiMilliseconds','expertVersion'}

def test_pins_and_environment_fail_closed(setup):
    _,app,_,db=setup;pin=reg.select(PRESETS[0]['entries'],db)
    with pytest.raises(Exception):reg.validate({**pin,'bundleHash':'wrong'},app.state.battles._lines(PRESETS[0]['entries']))
    with pytest.raises(Exception):reg.validate(pin,app.state.battles._lines(PRESETS[1]['entries']))
    reg.rollout_path(db).write_text('{bad');assert reg.select(PRESETS[0]['entries'],db) is None

def test_packaged_default_and_explicit_disable(setup):
    _,_,_,db=setup
    reg.rollout_path(db).unlink()
    assert reg.select(PRESETS[0]['entries'],db)['id']==PRESETS[0]['id']
    reg.rollout_path(db).write_text(json.dumps({'bundleHash':reg.bundle_hash(),'enabled':[]}))
    assert reg.select(PRESETS[0]['entries'],db) is None
