"""Manage H2 routing and publish the checked workspace to the local App runtime."""
import argparse,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from apps.api.expert_policies import registry as reg
from packages.sync.common import read,write

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['status','enable','disable','publish','rollback','diagnose'])
    ap.add_argument('--db',type=Path,default=ROOT/'var/app.sqlite')
    ap.add_argument('--home',type=Path,default=ROOT/'.catalog/sync')
    ap.add_argument('--release',help='Exact previously archived release for rollback')
    a=ap.parse_args();flag=reg.rollout_path(a.db)
    shipped=read(ROOT/'apps/api/h2-rollout.json')
    if a.command=='diagnose':
        from scripts.battle.h2_diagnostics import collect
        report=collect(ROOT)
        report['bundleMatchesShipped']=reg.bundle_hash()==shipped['bundleHash']
        report['environmentMatchesProfiles']=all(p.PROFILE['environmentHash']==reg.identity()[1] for p in reg.POLICIES.values())
        target=a.home/'h2-diagnostics.json';write(target,report)
        print(json.dumps({k:v for k,v in report.items() if k!='components'},ensure_ascii=True,indent=2))
        print('Report: '+str(target.resolve()));return
    if a.command=='status':
        print(json.dumps({'rollout':read(flag) if flag.exists() else shipped,'override':flag.exists()},ensure_ascii=False));return
    if a.command in ('enable','disable'):
        if a.command=='enable':
            if shipped['bundleHash']!=reg.bundle_hash() or any(p.PROFILE['environmentHash']!=reg.identity()[1] for p in reg.POLICIES.values()):
                raise SystemExit('Policy bundle or environment differs from the checked release; do not force enable.')
        write(flag,{'bundleHash':reg.bundle_hash(),'enabled':shipped['enabled'] if a.command=='enable' else []});print(a.command);return
    from packages.sync.service import SyncService
    from packages.sync.runtime import Worker
    service=SyncService(a.home,a.db);old=service.releases.current()
    if a.command=='rollback':
        if not a.release:ap.error('--release is required')
        service.rollback(a.release,old);print(a.release);return
    # Existing match histories need their original archived baseline. Never label
    # an unbound old history with the newly installed source implementation.
    if not old and a.db.exists():
        import sqlite3
        with sqlite3.connect(a.db) as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='matches'").fetchone()
            count=db.execute('SELECT count(*) FROM matches').fetchone()[0] if exists else 0
        if count and not (a.home/'baseline.json').exists():
            raise SystemExit('Archive the old baseline using the old source before upgrading an existing match database.')
    candidate=service.releases.archive(metadata={'kind':'h2-product','version':'h2-app-v1'})
    worker=Worker(service.releases,candidate,a.home/'staging'/('h2-smoke-'+uuid.uuid4().hex+'.sqlite'))
    cookie=''
    def call(path,body=None):
        nonlocal cookie
        reply=worker.request('POST','/api/battle'+path,{'cookie':cookie,'content-type':'application/json'},json.dumps(body or {}).encode())
        if reply['status'] not in (200,201):
            from scripts.battle.h2_diagnostics import smoke_error
            report=smoke_error(reply,path,candidate,a.home/'logs'/(candidate+'.log'))
            target=a.home/'h2-publish-failure.json';write(target,report)
            raise RuntimeError('Archived release smoke check failed: '+path+'; HTTP '+str(reply['status'])+'; '+json.dumps(report['error'],ensure_ascii=True)+'; report: '+str(target.resolve()))
        if 'set-cookie' in reply['headers']:cookie=reply['headers']['set-cookie'].split(';')[0]
        return json.loads(reply['body'])
    try:
        session=call('/session')
        assert len(session['presets'])==5
        for p in session['presets']:
            rev=call(f"/presets/{p['id']}/revision")
            v=call('/matches',{'requestId':uuid.uuid4().hex,'revisionId':rev['id'],'opponent':p['id']})
            # Session presets omit entries: inspect the shipped card lists locally.
            from packages.battle.presets import PRESETS
            entries=next(x['entries'] for x in PRESETS if x['id']==p['id'])
            pin=reg.select(entries,a.home/'staging'/'probe.sqlite')
            assert v['aiStrategy']['id']==(pin['id'] if pin else 'original-h2')
            call(f"/matches/{v['matchId']}/advance")
    finally:worker.close()
    backup=service.releases.backup_user(a.db)
    service.releases.activate(candidate,old,service.jobs.path)
    write(a.home/'h2-publication.json',{'releaseId':candidate,'previous':old,'backup':backup})
    print(json.dumps({'releaseId':candidate,'previous':old,'backup':backup}))
if __name__=='__main__':main()
