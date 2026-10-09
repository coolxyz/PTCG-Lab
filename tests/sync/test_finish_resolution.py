import copy
import pytest
from packages.sync.normalize import normalize
from packages.sync.identity import build_catalog
from packages.sync.finishes import refresh


def source():
    card={'id':2,'name':'测试卡','image':'img/1/ball.png','details':{'id':2,'cardName':'测试卡','collectionNumber':'002/151','commodityCode':'151C','cardType':'1','hp':90,'attribute':'1','evolveText':'基础','regulationMarkText':'G','special_shiny_type':1,'rarityText':'U★'}}
    pack={'id':1,'commodityCode':'151C','name':'151','cards':[card]}
    bundle=copy.deepcopy(pack);bundle.update(id=2,commodityCode='151PROMOf');bundle['cards'][0]['details'].update(special_shiny_type=0,rarityText='U');bundle['cards'][0]['image']='img/2/ball.png'
    return {'dict':{'card_type':[{'dictCode':'1','dictValue':'宝可梦'}],'attribute':[{'dictCode':'1','dictValue':'草'}]},'collections':[bundle,pack]}


@pytest.mark.parametrize('reverse',[False,True])
def test_expansion_finish_wins_over_bundle_only_with_identical_image(reverse):
    raw=source()
    if reverse:raw['collections'].reverse()
    original=copy.deepcopy(raw)
    n=normalize(raw,{'img/1/ball.png':'same','img/2/ball.png':'same'})
    card=n['cards']['2']
    assert (card['finishCode'],card['rarity'])==(1,'U★')
    assert card['finishResolution']['status']=='resolved'
    assert raw==original
    assert card['ruleHash']==normalize({'dict':raw['dict'],'collections':[original['collections'][0]]})['cards']['2']['ruleHash']


def test_conflicting_images_are_not_silently_called_ordinary():
    n=normalize(source(),{'img/1/ball.png':'one','img/2/ball.png':'two'})
    assert n['cards']['2']['finishCode'] is None
    assert n['cards']['2']['finishResolution']['status']=='needs-review'
    assert {'cardId':'2','code':'FINISH_CONFLICT'} in n['problems']


def test_import_and_refresh_choose_ordinary_without_changing_variant_ids():
    raw=source();normal=copy.deepcopy(raw['collections'][1]['cards'][0])
    normal['id']=normal['details']['id']=1;normal['image']='img/1/normal.png'
    normal['details'].update(special_shiny_type=0,rarityText='U')
    raw['collections'][1]['cards'].append(normal)
    n=normalize(raw,{'img/1/ball.png':'same','img/2/ball.png':'same','img/1/normal.png':'normal'})
    # Fix both source IDs to the same existing printing, as in the live catalogue.
    baseline={'cards':[{'printingId':'CN:151C:002','cnName':'测试卡','productCode':'151C','collectorNumber':'002','images':[]}],'products':[]}
    mapping={'cards':{'1':{'printingId':'CN:151C:002'},'2':{'printingId':'CN:151C:002'}}}
    result=build_catalog(n,baseline,{},'a'*40,mappings=mapping)
    card=result['catalog']['cards'][0]
    assert card['images'][0]['variantId']=='chs-variant:1'
    assert {v['finishLabel'] for v in card['variants']}=={'普通版','精灵球版'}
    before=copy.deepcopy(card);refresh(card)
    assert card==before
    assert {v['variantId'] for v in card['variants']}=={'chs-variant:1','chs-variant:2'}
