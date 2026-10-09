"""Explicit offline, backed-up removal of non-CHS catalogue records and references.

Prepare first, inspect its report, stop the service, then apply that preparation.
Historical runtime directories are moved to the backup, never mutated in place.
"""
from pathlib import Path
import argparse
import copy
from contextlib import closing
import json
import shutil
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.sync.common import read, write, digest, now, REPOSITORY
from packages.sync.identity import sections
from packages.sync.releases import Releases, DATA_FILES
from packages.sync.jobs import Jobs
from packages.sync.runtime import Worker
from packages.sync.service import SyncService


def checked(path):
    path = Path(path).resolve()
    if ROOT not in path.parents or path.is_symlink():
        raise ValueError("Path outside workspace")
    return path


def has_removed(value, removed):
    if isinstance(value, dict):
        return any(k in removed or has_removed(v, removed) for k, v in value.items())
    if isinstance(value, list):
        return any(has_removed(v, removed) for v in value)
    return isinstance(value, str) and value in removed


def clean_database(database, removed):
    counts = {}
    with closing(sqlite3.connect(database)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def delete(table, column, ids):
            if table not in tables:
                return
            before = db.total_changes
            db.executemany(f'DELETE FROM "{table}" WHERE "{column}"=?', [(i,) for i in ids])
            counts[table] = counts.get(table, 0) + db.total_changes - before
        decks = {i for i, body in db.execute('SELECT id,entries FROM decks') if has_removed(json.loads(body), removed)}
        revisions = {i for i, deck, body in db.execute('SELECT id,deck_id,body FROM revisions') if deck in decks or has_removed(json.loads(body), removed)}
        matches = {i for i, revision, body in db.execute('SELECT id,revision_id,body FROM matches') if revision in revisions or has_removed(json.loads(body), removed)}
        for table in ('match_frames', 'match_commands', 'match_ai_views'):
            delete(table, 'match_id', matches)
        delete('matches', 'id', matches)
        delete('revisions', 'id', revisions)
        delete('decks', 'id', decks)
        delete('collection_variants', 'printing_id', removed)
        delete('collection', 'printing_id', removed)
        delete('card_images', 'printing_id', removed)
        imports = {i for i, body in db.execute('SELECT id,body FROM imports') if has_removed(json.loads(body), removed)}
        changes = {i for i, batch, body in db.execute('SELECT id,batch_id,body FROM changes') if batch in imports or has_removed(json.loads(body), removed)}
        delete('changes', 'id', changes)
        delete('imports', 'id', imports)
        db.execute("UPDATE meta SET value=value+1 WHERE key='collectionVersion'")
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT count(*) FROM matches').fetchone()[0] == 0, 'Remaining matches need their old runtimes; do not purge archives'
        assert db.execute('SELECT count(*) FROM revisions').fetchone()[0] == 0, 'Remaining frozen revisions need explicit handling'
    return counts


def prepare():
    home = ROOT / '.catalog/sync'
    old = Releases(home)
    previous = old.current()
    manifest = old.verify(previous)
    commit = manifest['metadata']['commit']
    base = old.path(previous)
    catalog = read(base / 'data/catalog/catalog.json')
    normalized = read(home / 'snapshots' / commit / 'normalized.json')
    kept = {c['printingId'] for c in catalog['cards'] if any(cid in normalized['cards'] for cid in c.get('upstreamFaces', {})) and not c.get('upstreamRemoved')}
    removed = {c['printingId'] for c in catalog['cards']} - kept
    source_url = f'{REPOSITORY}/blob/{commit}/ptcg_chs_infos.json'
    products = {p['id']: {'id': p['id'], 'name': p['name'], 'title': p['name'], 'releasedAt': p['releasedAt'], 'status': 'released', 'upstreamCode': p['code'], 'series': p['series'], 'source': {'url': source_url}} for p in normalized['products'].values()}
    details = {'cards': {}}
    catalog['cards'] = [c for c in catalog['cards'] if c['printingId'] in kept]
    for c in catalog['cards']:
        ids = sorted(set(c['upstreamFaces']) & set(normalized['cards']), key=int)
        up = normalized['cards'][ids[0]]
        c['variants'] = [v for v in c.get('variants', []) if v['upstreamId'] in ids]
        c['productIds'] = sorted({p for v in c['variants'] for p in v['productIds']} & products.keys())
        product = products[c['productIds'][0]]
        c.update(cnName=up['name'], printedNumber=up['number'], collectorNumber=up['number'].split('/')[0], productName=product['name'], productCode=product['upstreamCode'], sourceEvidence=[source_url])
        c['images'] = [v['image'] for v in c['variants'] if v.get('image')]
        c['catalogStatus'] = 'upstream-listed'
        c.pop('source', None)
        details['cards'][c['printingId']] = {'sections': sections(up['face']), 'source': source_url, 'textStatus': 'upstream-available', 'upstreamCardId': ids[0]}
    catalog['products'] = list(products.values())
    catalog['templates'] = [t for t in catalog['templates'] if all(e['printingId'] in kept for e in t['entries'])]
    catalog['sourcePolicy'] = 'PTCG-CHS-Datasets-only'
    catalog['version'] = digest({k: v for k, v in catalog.items() if k != 'version'})
    SyncService.validate_catalog(catalog)
    work = ROOT / 'var' / ('source-cleanup-' + uuid.uuid4().hex)
    work.mkdir()
    stage_root = work / 'root'
    for folder in ('packages', 'apps/api', 'runtime/engine'):
        shutil.copytree(ROOT / folder, stage_root / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in DATA_FILES:
        destination = stage_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / name, destination)
    # Retain executable implementations but remove their old printing aliases.
    effects = read(base / 'data/simulation/effects.json')
    for effect in effects['effects']:
        effect['printings'] = [p for p in effect['printings'] if p in kept]
    effects['effects'] = [e for e in effects['effects'] if e['printings']]
    plain = read(base / 'data/cardpool/plain-pokemon.json')
    for rows in plain.values():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and 'printings' in row:
                    row['printings'] = [p for p in row['printings'] if p in kept]
    admission = read(stage_root / 'data/engine/catalog.json')
    admission['printings'] = {p: c for p, c in admission['printings'].items() if p in kept}
    admission['decks'] = {}
    admission['acceptedDeckSource'] = source_url
    admission['metadataCorroboration'] = {}
    write(stage_root / 'data/engine/catalog.json', admission)
    exceptions = read(stage_root / 'data/cardpool/source-exceptions.json')
    exceptions['printingIds'] = [p for p in exceptions['printingIds'] if p in kept]
    write(stage_root / 'data/cardpool/source-exceptions.json', exceptions)
    new_home = work / 'sync'
    releases = Releases(new_home, stage_root)
    jobs = Jobs(new_home)
    job = jobs.create(digest({'cleanup': previous, 'catalog': catalog['version']}), {'kind': 'source-cleanup', 'commit': commit, 'asOf': catalog['asOf']})
    rid = releases.archive(catalog, details, effects, plain, {'kind': 'source-cleanup', 'commit': commit, 'jobId': job['id'], 'source': REPOSITORY})
    old_job = manifest['metadata'].get('jobId')
    if old_job:
        migration = read(home / 'results' / old_job / 'migration.json')
        migration.update(catalog=catalog, details=details)
        migration['mappings'] = {k: v for k, v in migration['mappings'].items() if v['printingId'] in kept}
        write(new_home / 'results' / job['id'] / 'migration.json', migration)
        tasks = read(home / 'results' / old_job / 'tasks.json')
        write(new_home / 'results' / job['id'] / 'tasks.json', tasks)
    shutil.copytree(home / 'snapshots' / commit, new_home / 'snapshots' / commit)
    shutil.copytree(home / 'images', new_home / 'images')
    write(new_home / 'identity-archive.json', {c['printingId']: c for c in catalog['cards']})
    releases.activate(rid, None, jobs.path)
    write(new_home / 'baseline.json', {'releaseId': rid})
    jobs.update(job['id'], state='partial', stage='published', releaseId=rid, catalogReleaseStatus='published', battleReleaseStatus='unchanged', report={'removed': len(removed), 'retained': len(kept)})
    with closing(sqlite3.connect(ROOT / 'var/app.sqlite')) as src, closing(sqlite3.connect(work / 'user.sqlite')) as dst:
        src.backup(dst)
    counts = clean_database(work / 'user.sqlite', removed)
    worker = Worker(releases, rid, work / 'health.sqlite')
    try:
        response = worker.request('GET', '/api/meta')
        assert response['status'] == 200, response
        meta = json.loads(response['body'])
        assert meta['cardCount'] == len(kept)
        session = worker.request('POST', '/api/battle/session', {'Content-Type': 'application/json'}, b'{}')
        assert session['status'] == 200, session
        assert json.loads(session['body'])['opponents'] == []
    finally:
        worker.close()
    report = {'preparedAt': now(), 'work': str(work), 'previous': previous, 'releaseId': rid, 'commit': commit, 'before': len(kept) + len(removed), 'retained': len(kept), 'removed': len(removed), 'products': len(products), 'templates': len(catalog['templates']), 'deletedUserRows': counts, 'verifiedCount': meta['verifiedCount'], 'healthPassed': True}
    write(work / 'report.json', report)
    write(work / 'removed.json', sorted(removed))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def apply(work, resume_backup=None):
    work = checked(work)
    report = read(work / 'report.json')
    assert (work / 'sync').exists(), 'Preparation has already been installed'
    if resume_backup:
        backup = checked(resume_backup)
        assert read(backup / 'restore.json')['previous'] == report['previous']
        assert read(backup / '.catalog/sync/current.json')['releaseId'] == report['previous']
    else:
        assert Releases(ROOT / '.catalog/sync').current() == report['previous']
        backup = checked(ROOT / 'exports' / ('source-cleanup-' + uuid.uuid4().hex))
        backup.mkdir(parents=True)
        shutil.copy2(ROOT / 'var/app.sqlite', backup / 'user.sqlite')
    upload = ROOT / 'var/app-card-images'
    if upload.exists() and not resume_backup:
        shutil.copytree(upload, backup / 'user-card-images')
    # Recompute against the stopped service's final database, not the preview copy.
    with closing(sqlite3.connect(ROOT / 'var/app.sqlite')) as src, closing(sqlite3.connect(work / 'apply-user.sqlite')) as dst:
        src.backup(dst)
    report['deletedUserRows'] = clean_database(work / 'apply-user.sqlite', set(read(work / 'removed.json')))
    with closing(sqlite3.connect(backup / 'user.sqlite')) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    write(backup / 'restore.json', {'createdAt': now(), 'previous': report['previous'], 'note': 'Original catalog and sync directories retain their workspace-relative paths. user.sqlite is the original var/app.sqlite.'})
    def move(path):
        path = checked(path)
        if not path.exists():
            return
        destination = checked(backup / path.relative_to(ROOT))
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
    # All legacy catalogue files and live/experimental runtime archives leave the
    # application tree. The downloaded, user-selected upstream stays in place.
    for path in list((ROOT / '.catalog').iterdir()):
        if path.name != 'upstream':
            move(path)
    move(ROOT / 'data/catalog')
    move(ROOT / 'data/collection/catalog.json')
    for name in ('data/engine/catalog.json', 'data/simulation/effects.json', 'data/cardpool/plain-pokemon.json', 'data/cardpool/source-exceptions.json', 'data/cardpool/release-history', 'data/cardpool/source-index.json', 'data/cardpool/reprints.json', 'data/cardpool/test-decks.json'):
        move(ROOT / name)
    (work / 'sync').rename(ROOT / '.catalog/sync')
    release = Releases(ROOT / '.catalog/sync').path(report['releaseId'])
    shutil.copytree(release / 'data/catalog', ROOT / 'data/catalog')
    for name in ('data/engine/catalog.json', 'data/simulation/effects.json', 'data/cardpool/plain-pokemon.json', 'data/cardpool/source-exceptions.json'):
        shutil.copy2(release / name, ROOT / name)
    shutil.copy2(work / 'apply-user.sqlite', ROOT / 'var/app.sqlite')
    with closing(sqlite3.connect(ROOT / 'var/app.sqlite')) as db:
        files = {r[0] for r in db.execute('SELECT filename FROM card_images')}
    if upload.exists():
        for path in list(upload.iterdir()):
            if path.is_file() and path.name not in files:
                move(path)
    report.update(appliedAt=now(), backup=str(backup))
    move(work)
    write(backup / 'cleanup-report.json', report)
    write(ROOT / 'artifacts/sync/source-cleanup.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', metavar='PREPARATION_DIRECTORY')
    parser.add_argument('--resume-backup', metavar='BACKUP_DIRECTORY')
    args = parser.parse_args()
    apply(args.apply, args.resume_backup) if args.apply else prepare()
