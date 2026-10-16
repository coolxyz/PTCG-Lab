"""Manage H2 routing and publish the checked workspace to the local App runtime."""
import argparse,json,os,subprocess,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from apps.api.expert_policies import registry as reg
from packages.sync.common import read,write


def expected_strategies(release_root, database):
    """Evaluate routing inside the candidate, including its copied effect manifest."""
    code = (
        'import json,sys; '
        'from apps.api.expert_policies import registry; '
        'from packages.battle.presets import PRESETS; '
        'print(json.dumps({p["id"]:registry.public(registry.select(p["entries"],sys.argv[1]))["id"] '
        'for p in PRESETS}))'
    )
    output = subprocess.check_output(
        [sys.executable, '-X', 'utf8', '-c', code, str(database.resolve())],
        cwd=release_root,
        env={**os.environ, 'PYTHONPATH': str(release_root)},
        text=True,
        encoding='utf-8',
    )
    return json.loads(output)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['status','enable','disable','publish','rollback'])
    ap.add_argument('--db',type=Path,default=ROOT/'var/app.sqlite')
    ap.add_argument('--home',type=Path,default=ROOT/'.catalog/sync')
    ap.add_argument('--release',help='Exact previously archived release for rollback')
    ap.add_argument('--force', action='store_true', help='Publish only: rebind the copied effect manifest to the current local engine; retain smoke checks and backups')
    a=ap.parse_args();flag=reg.rollout_path(a.db)
    if a.force and a.command != 'publish':
        ap.error('--force is only supported with publish')
    shipped=read(ROOT/'apps/api/h2-rollout.json')
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
    metadata={'kind':'h2-product','version':'h2-app-v1'}
    archive_args={}
    if a.force:
        # Rebind only the candidate copy. Preserve workspace assets and old
        # releases, and keep the worker's normal runtime and API checks.
        effects=read(ROOT/'data/simulation/effects.json')
        metadata.update(forced=True,sourceEffectEngineVersion=effects['engineVersion'])
        archive_args['effects']=effects
        print('Force publish: rebinding the copied effect manifest; five-preset smoke checks remain required.',file=sys.stderr)
    candidate=service.releases.archive(metadata=metadata,**archive_args)
    smoke_db=a.home/'staging'/('h2-smoke-'+uuid.uuid4().hex+'.sqlite')
    expected=expected_strategies(service.releases.path(candidate),smoke_db)
    worker=Worker(service.releases,candidate,smoke_db)
    cookie=''
    def call(path,body=None):
        nonlocal cookie
        reply=worker.request('POST','/api/battle'+path,{'cookie':cookie,'content-type':'application/json'},json.dumps(body or {}).encode())
        if reply['status'] not in (200,201):raise RuntimeError('Archived release smoke check failed: '+path)
        if 'set-cookie' in reply['headers']:cookie=reply['headers']['set-cookie'].split(';')[0]
        return json.loads(reply['body'])
    try:
        session=call('/session')
        assert len(session['presets'])==5
        for p in session['presets']:
            rev=call(f"/presets/{p['id']}/revision")
            v=call('/matches',{'requestId':uuid.uuid4().hex,'revisionId':rev['id'],'opponent':p['id']})
            assert v['aiStrategy']['id']==expected[p['id']]
            call(f"/matches/{v['matchId']}/advance")
    finally:worker.close()
    backup=service.releases.backup_user(a.db)
    service.releases.activate(candidate,old,service.jobs.path)
    publication={'releaseId':candidate,'previous':old,'backup':backup}
    if a.force:
        publication.update(forced=True,sourceEffectEngineVersion=metadata['sourceEffectEngineVersion'],engineVersion=service.releases.manifest(candidate)['engineVersion'],strategies=expected)
    write(a.home/'h2-publication.json',publication)
    print(json.dumps(publication))
if __name__=='__main__':main()
