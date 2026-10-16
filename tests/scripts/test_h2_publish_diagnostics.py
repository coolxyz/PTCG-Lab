import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from scripts.battle.h2_diagnostics import collect, smoke_error


def workspace(tmp_path):
    files=['packages/rules/rule.py','packages/engine_adapter/base.py','packages/engine_adapter/cn_format.py','scripts/engine/build_engine.py','data/engine/catalog.json','rulesets/cn-standard-2026-09-16.json','data/cardpool/plain-pokemon.json']
    for name in files:
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'{}\n')
    overlay=tmp_path/'runtime/engine/test.py';overlay.parent.mkdir(parents=True);overlay.write_bytes(b'pass\r\n')
    p=tmp_path/'artifacts/engine/overlay-hashes.json';p.parent.mkdir(parents=True);p.write_text(json.dumps({'test.py':hashlib.sha256(overlay.read_bytes()).hexdigest()}))
    effect=tmp_path/'data/simulation/effects.json';effect.parent.mkdir(parents=True);effect.write_text('{"engineVersion":"old"}')
    return effect


def test_diagnosis_normalizes_source_newlines_but_detects_overlay_bytes(tmp_path):
    effect=workspace(tmp_path);original=collect(tmp_path)
    assert not original['engineMatchesEffects'] and original['overlayFilesWithCRLF']==1
    assert original['overlayMismatches']==[]
    p=tmp_path/'packages/rules/rule.py';p.write_bytes(b'{}\r\n')
    after=collect(tmp_path)
    assert original['runtimeEngineVersion']==after['runtimeEngineVersion']
    assert after['components']['packages/rules/rule.py']['crlf']==1
    (tmp_path/'runtime/engine/test.py').write_bytes(b'pass\n')
    assert collect(tmp_path)['overlayMismatches']==['test.py']
    assert json.loads(effect.read_text())['engineVersion']=='old' # read-only, never rebind


@pytest.mark.parametrize('status,body',[(409,{'code':'EFFECT_ENGINE_MISMATCH','message':'效果发布与引擎版本不一致'}),(500,{'code':'WORKER_ERROR','message':'Version worker failed'})])
def test_publish_failure_reports_details_and_never_activates(tmp_path,monkeypatch,status,body):
    from scripts.battle import h2_manage
    from packages.sync import service,runtime
    releases=SimpleNamespace(current=lambda:'old',archive=lambda **kw:'candidate',activate=Mock(),backup_user=Mock())
    sync=SimpleNamespace(releases=releases,jobs=SimpleNamespace(path=tmp_path/'jobs.sqlite'))
    class Worker:
        closed=False
        def request(self,method,path,headers,body):
            if path.endswith('/session'):return {'status':200,'headers':{'set-cookie':'test=secret'},'body':json.dumps({'presets':[{'id':str(i)} for i in range(5)]}).encode()}
            if path.endswith('/revision'):return {'status':200,'headers':{},'body':b'{"id":"revision"}'}
            return {'status':status,'headers':{},'body':json.dumps(error_body).encode()}
        def close(self):self.closed=True
    error_body=body;worker=Worker()
    monkeypatch.setattr(service,'SyncService',lambda *a:sync)
    monkeypatch.setattr(runtime,'Worker',lambda *a:worker)
    monkeypatch.setattr('sys.argv',['h2_manage.py','publish','--home',str(tmp_path),'--db',str(tmp_path/'user.sqlite')])
    with pytest.raises(RuntimeError,match=body['code']):h2_manage.main()
    report=json.loads((tmp_path/'h2-publish-failure.json').read_text())
    assert report['status']==status and report['error']==body and report['endpoint']=='/matches'
    assert report['workerLog']==str(tmp_path/'logs/candidate.log')
    assert 'secret' not in json.dumps(report) and worker.closed
    releases.activate.assert_not_called();releases.backup_user.assert_not_called()


def test_non_json_worker_response_is_bounded():
    r=smoke_error({'status':502,'body':b'x'*10000},'/matches','candidate',Path('logs'))
    assert len(r['error']['message'])==2000
