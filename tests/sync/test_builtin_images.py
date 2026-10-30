"""Fresh profiles use shipped pinned face IDs without a data sync or Git runtime."""
from hashlib import sha256
from types import SimpleNamespace
import io,json
from PIL import Image
import pytest
from packages.sync.artwork import ArtworkCache,DEFAULTS
from packages.sync.builtin_images import BuiltinImages,INDEX
from packages.sync.http_source import object_sha
from packages.sync.images import Images
from packages.sync.common import SyncError


def test_fresh_profile_local_and_raw_faces_use_pinned_repository_and_path(tmp_path):
    root=tmp_path.resolve();catalog=root/'data/catalog/catalog.json';catalog.parent.mkdir(parents=True);catalog.write_text('{}')
    clone=root/'clone';(clone/'.git').mkdir(parents=True);(clone/'img').mkdir()
    stream=io.BytesIO();Image.new('RGB',(4,6),'purple').save(stream,'PNG');raw=stream.getvalue()
    (clone/'img/face.png').write_bytes(raw)
    value={'schema':1,'repository':'https://github.com/example/Cards','commit':'a'*40,'catalogFileSha256':sha256(catalog.read_bytes()).hexdigest(),
           'images':{'12':{'path':'img/face.png','blob':object_sha('blob',raw)}}}
    file=root/INDEX;file.parent.mkdir(parents=True);file.write_text(json.dumps(value))
    builtin=BuiltinImages(root)
    cache=ArtworkCache(root/'profile',transport=lambda *a:pytest.fail('No network'))
    cache.configure({**DEFAULTS,'source':'local-git','localGitPath':str(clone)})
    images=Images(cache.home,SimpleNamespace(repository='https://github.com/wrong/Repo'),cache=cache,builtin=builtin)
    assert images.available('a'*40,'12') and not images.installed('a'*40)
    assert images.data('a'*40,'12')==(raw,'image/png')
    assert images.get('a'*40,'12')[0]==clone/'img/face.png'
    assert cache.status()['cachedImages']==0
    calls=[];cache.transport=lambda url,limit:calls.append(url) or raw
    cache.configure(DEFAULTS)
    assert images.data('a'*40,'12')[0]==raw
    assert calls==['https://raw.githubusercontent.com/example/Cards/'+('a'*40)+'/img/face.png']
    assert not images.available('b'*40,'12') and not images.available('a'*40,'999')
    catalog.write_text('{"changed":true}')
    with pytest.raises(SyncError,match='卡表不匹配'):BuiltinImages(root)


def test_generator_uses_sync_canonical_face_for_duplicate_product_occurrences(tmp_path,monkeypatch):
    from scripts.product import build_image_index as tool
    from packages.sync.normalize import normalize
    card={'id':12,'image':'img/first.png','details':{'id':12,'cardName':'测试牌','cardType':'POKEMON','collectionNumber':'001'}}
    other={**card,'image':'img/bundle.png'}
    upstream={'dict':{},'collections':[{'id':1,'commodityCode':'A','name':'A','cards':[card]},
                                       {'id':2,'commodityCode':'B','name':'B','cards':[other]}]}
    raw=json.dumps(upstream).encode();tree={'ptcg_chs_infos.json':object_sha('blob',raw),'img/first.png':'1'*40,'img/bundle.png':'2'*40}
    class Cache:
        def initialize(self):pass
        def git(self,*args,**kwargs):return b'a'*40
        def tree(self,commit):return tree
        def prefetch(self,blobs):assert blobs==[tree['ptcg_chs_infos.json']]
        def blob(self,sha,limit):assert sha==tree['ptcg_chs_infos.json'];return raw
    monkeypatch.setattr(tool,'GitCache',lambda *a:Cache())
    catalog=tmp_path/'catalog.json';catalog.write_text('{}');output=tmp_path/'index.json'
    report=tool.build_snapshot(catalog,tmp_path/'cache','a'*40,output,'https://github.com/example/Cards')
    images=json.loads(output.read_text())['images'];canonical=normalize(upstream,tree)['cards']['12']
    assert images=={'12':{'path':canonical['imagePath'],'blob':canonical['imageBlob']}}
    assert report['faces']==1 and report['imageDownloads']==0
