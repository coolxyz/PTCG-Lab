from hashlib import sha256
import json
import pytest
from scripts.packaging.mobile_assets import build,bundle


def inputs(tmp_path):
    assets=tmp_path/'approved';root=tmp_path/'source'
    (assets/'data/catalog').mkdir(parents=True);(root/'resources/artwork').mkdir(parents=True)
    catalog={'version':'version-1','cards':[{'printingId':'P1','engineId':'E1','cnName':'测试牌','category':'POKEMON','isBasicPokemon':True,'internalResearch':'must not export'}]}
    raw=json.dumps(catalog).encode();(assets/'data/catalog/catalog.json').write_bytes(raw)
    (assets/'data/catalog/card-details.json').write_text(json.dumps({'cards':{'P1':{'sections':[{'kind':'招式','name':'测试','text':'抽一张牌。'}]}}}))
    pin={'schema':1,'repository':'https://github.com/example/Cards','commit':'a'*40,'catalogFileSha256':sha256(raw).hexdigest()}
    (root/'resources/artwork/builtin-index.json').write_text(json.dumps({**pin,'cards':[{'printingId':'P1','engineId':'E1','path':'img/1/1.png','blob':'b'*40}]}))
    (root/'resources/artwork/builtin-snapshot.json').write_text(json.dumps({**pin,'images':{}}))
    (assets/'runtime').mkdir();(assets/'runtime/engine').write_bytes(b'must not export engine')
    (assets/'private.sqlite').write_bytes(b'must not export user data')
    return assets,root


def test_mobile_kit_contains_only_bound_display_metadata(tmp_path):
    assets,root=inputs(tmp_path);out=tmp_path/'output'
    report=build(assets,out,root=root)
    assert report['cards']==report['indexedArtwork']==1
    assert not report['includesRules'] and not report['includesUserData'] and report['imageDownloads']==0
    assert {p.name for p in out.iterdir()}=={'catalog.json','artwork.json','manifest.json'}
    shown=json.loads((out/'catalog.json').read_text())
    from ptcg_engine_provider.catalog import MemoryCatalog
    original=json.loads((assets/'data/catalog/catalog.json').read_text())
    assert shown['displayCatalogVersion']==MemoryCatalog.from_catalog(original).catalog_version
    assert shown['version']==original['version']
    assert shown['cards'][0]['chineseDetails']['sections'][0]['text']=='抽一张牌。'
    assert 'internalResearch' not in shown['cards'][0]
    manifest=json.loads((out/'manifest.json').read_text())
    for name,digest in manifest['files'].items():assert sha256((out/name).read_bytes()).hexdigest()==digest


def test_mismatched_catalog_fails_before_output_creation(tmp_path):
    assets,root=inputs(tmp_path);(assets/'data/catalog/catalog.json').write_text('{}');out=tmp_path/'output'
    with pytest.raises(ValueError,match='do not match'):build(assets,out,root=root)
    assert not out.exists()


def test_wrong_engine_identity_cannot_select_artwork(tmp_path):
    assets,root=inputs(tmp_path);index=root/'resources/artwork/builtin-index.json'
    value=json.loads(index.read_text());value['cards'][0]['engineId']='different';index.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='identity differs'):build(assets,tmp_path/'output',root=root)


def test_asset_source_cannot_be_used_as_output_parent(tmp_path):
    assets,root=inputs(tmp_path)
    with pytest.raises(ValueError,match='separate directories'):build(assets,assets/'generated',root=root)
    assert not (assets/'generated').exists()


def test_mobile_update_document_has_exact_checksums_and_no_rules_images_or_user_data(tmp_path):
    assets,root=inputs(tmp_path);resources=tmp_path/'resources';build(assets,resources,root=root)
    report=bundle(resources,tmp_path/'release')
    raw=(tmp_path/'release/mobile-display.json').read_bytes()
    assert sha256(raw).hexdigest()==report['sha256']
    value=json.loads(raw);assert value['kind']=='ptcg-mobile-display'
    assert set(value)=={'schema','kind','catalog','artwork','manifest'}
    for name in ('catalog','artwork'):
        assert sha256(value[name].encode()).hexdigest()==json.loads(value['manifest'])['files'][name+'.json']
    assert not report['includesRules'] and not report['includesUserData'] and not report['includesImages']
    assert (tmp_path/'release/mobile-display.json.sha256').read_text().startswith(report['sha256'])


def test_tampered_mobile_update_resources_are_not_packaged(tmp_path):
    assets,root=inputs(tmp_path);resources=tmp_path/'resources';build(assets,resources,root=root)
    (resources/'catalog.json').write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):bundle(resources,tmp_path/'release')
    assert not (tmp_path/'release').exists()
