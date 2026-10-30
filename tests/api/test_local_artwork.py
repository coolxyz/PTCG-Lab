"""Product API exposes just online cache and a checked-out local clone."""
from hashlib import sha256
import io
import json
import sqlite3
from PIL import Image
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import CARDS, RAW, ROOT
from packages.sync.http_source import object_sha


def setup_local(tmp_path, monkeypatch):
    import packages.sync.artwork as module
    root = tmp_path.resolve()
    clone = root / 'clone'
    (clone / '.git').mkdir(parents=True)
    (clone / 'img').mkdir()
    stream=io.BytesIO(); Image.new('RGB',(5,8),'purple').save(stream,'PNG'); raw=stream.getvalue()
    (clone / 'img/one.png').write_bytes(raw)
    card=next(c for c in CARDS.values() if c.get('engineId'))
    index=root/'index.json'
    index.write_text(json.dumps({'schema':1,'repository':'https://github.com/example/Cards','commit':'a'*40,
        'catalogFileSha256':sha256((ROOT/'data/catalog/catalog.json').read_bytes()).hexdigest(),
        'cards':[{'printingId':card['printingId'],'engineId':card['engineId'],'path':'img/one.png','blob':object_sha('blob',raw)}]}))
    monkeypatch.setattr(module,'INDEX',str(index))
    import packages.sync.builtin_images as face_module
    monkeypatch.setattr(face_module,'INDEX',str(root/'no-builtin-faces.json'))
    return clone,card,raw


def test_local_settings_check_library_online_and_reopen(tmp_path,monkeypatch):
    from apps.api.product_online import ProductResources
    from ptcg_desktop_api.resources import Resources
    from ptcg_engine_provider.catalog import MemoryCatalog
    clone,card,raw=setup_local(tmp_path,monkeypatch)
    db=tmp_path.resolve()/'app.sqlite'
    for attempt in range(2):
        app=create_app(db)
        app.state.sync.artwork_cache.transport=lambda *a: (_ for _ in ()).throw(AssertionError('No network'))
        with TestClient(app) as client:
            check=client.post('/api/sync/artwork/local-check',json={'path':str(clone)})
            assert check.status_code==200 and check.json()['checkedImages']==1
            if attempt==0:
                response=client.put('/api/sync/artwork/settings',json={'source':'local-git','localGitPath':str(clone),'baseUrl':'','capacityMiB':1024})
                assert response.status_code==200
            assert client.get('/api/sync/artwork/settings').json()['localGitAvailable']
            shown=next(c for c in client.get('/api/cards').json()['cards'] if c['printingId']==card['printingId'])
            assert shown['image']['url'].startswith('/api/sync/artwork/')
            image=client.get(shown['image']['url']);assert image.status_code==200 and image.content==raw
            assert client.get('/api/sync/artwork/settings').json()['cachedImages']==0
            online=ProductResources(app.state.card_media,Resources(tmp_path.resolve()/f'assets-{attempt}'),app.state.sync.images)
            metadata=online.catalog(MemoryCatalog.from_catalog(RAW).catalog_version,'unavailable')
            row=next(c for c in metadata['cards'] if c['engineId']==card['engineId'])
            assert row['image']['url']==shown['image']['url']
            assert client.put('/api/sync/artwork/settings',json={'source':'local-git','localGitPath':str(clone),'baseUrl':'','capacityMiB':1024},headers={'Origin':'https://evil.invalid'}).status_code==403


def test_zip_upload_endpoints_and_old_overrides_are_removed(tmp_path,monkeypatch):
    clone,card,raw=setup_local(tmp_path,monkeypatch)
    db=tmp_path.resolve()/'app.sqlite'
    with sqlite3.connect(db) as sql:
        sql.execute('CREATE TABLE card_images(printing_id TEXT PRIMARY KEY, filename TEXT)')
        sql.execute('INSERT INTO card_images VALUES(?,?)',(card['printingId'],'legacy.png'))
    legacy=tmp_path/'app-card-images/legacy.png';legacy.parent.mkdir();legacy.write_bytes(raw)
    display=tmp_path/'app.sync/display';display.mkdir(parents=True);(display/'index.sqlite').write_bytes(b'obsolete display data')
    with TestClient(create_app(db)) as client:
        for method,path in [('post','/api/sync/resources/install'),('post','/api/sync/resources/activate'),('get','/api/sync/resources'),('get','/api/sync/resource-images/a/b'),('put',f"/api/cards/{card['printingId']}/image"),('delete',f"/api/cards/{card['printingId']}/image"),('get','/uploaded-card-images/legacy.png'),('get','/card-images/legacy.png')]:
            assert getattr(client,method)(path).status_code==404,path
        assert client.get('/api/cards').status_code==200
        assert legacy.read_bytes()==raw and (display/'index.sqlite').read_bytes()==b'obsolete display data'
        assert '/api/sync/resources/install' not in client.get('/openapi.json').json()['paths']


def test_fresh_profile_reads_bundled_face_without_sync(tmp_path,monkeypatch):
    clone,card,raw=setup_local(tmp_path,monkeypatch)
    import packages.sync.builtin_images as module
    snapshot=tmp_path/'faces.json'
    snapshot.write_text(json.dumps({'schema':1,'repository':'https://github.com/example/Cards','commit':'b'*40,
        'catalogFileSha256':sha256((ROOT/'data/catalog/catalog.json').read_bytes()).hexdigest(),
        'images':{'999':{'path':'img/one.png','blob':object_sha('blob',raw)}}}))
    monkeypatch.setattr(module,'INDEX',str(snapshot))
    app=create_app(tmp_path.resolve()/'fresh.sqlite')
    app.state.sync.artwork_cache.transport=lambda *a: (_ for _ in ()).throw(AssertionError('No network'))
    with TestClient(app) as client:
        response=client.put('/api/sync/artwork/settings',json={'source':'local-git','localGitPath':str(clone),'baseUrl':'','capacityMiB':1024})
        assert response.status_code==200 and response.json()['indexedFaces']==1
        image=client.get('/api/sync/images/'+('b'*40)+'/999')
        assert image.status_code==200 and image.content==raw
        assert not app.state.sync.images.installed('b'*40)
        assert client.get('/api/sync/images/'+('b'*40)+'/998').status_code==404
