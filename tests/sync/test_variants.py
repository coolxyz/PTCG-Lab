import copy
import pytest
from apps.api.main import create_app
from packages.collection.domain import CARDS, DomainError
from packages.sync.common import write, SyncError
from packages.sync.variants import Variants


def test_variant_transfer_preserves_totals_and_legacy_writes(tmp_path):
    app = create_app(tmp_path / "user.sqlite")
    store, service = app.state.store, app.state.sync
    card = copy.deepcopy(next(iter(CARDS.values())))
    pid = card["printingId"]
    card["variants"] = [{"variantId": "chs-variant:1"}, {"variantId": "chs-variant:2"}]
    write(service.home / "identity-archive.json", {pid: card})
    variants = Variants(service)
    # Override only this test's source identity, not any runtime card registry.
    variants.catalog = lambda: {pid: card}
    change = {"printingId": pid, "condition": "全新", "quantity": 4, "notes": "保留", "wishlist": False}
    store.update_collection([change], 0)
    result = variants.set(pid, "全新", "chs-variant:1", 3, 1)
    assert result["entries"][0]["unassigned"] == 1
    assert store.collection()["entries"][0]["quantity"] == 4
    with pytest.raises(SyncError, match="未分配数量不足"):
        variants.set(pid, "全新", "chs-variant:2", 2, 2)
    with pytest.raises(DomainError):
        store.update_collection([{**change, "quantity": 2}], 2)
    assert store.collection()["version"] == 2
    variants.set(pid, "全新", "chs-variant:1", 1, 2)
    store.update_collection([{**change, "quantity": 2}], 3)
    assert variants.view()["entries"][0]["unassigned"] == 1
    assert store.collection()["entries"][0]["notes"] == "保留"

def test_finish_holdings_atomic_totals_and_conditions(tmp_path):
    app=create_app(tmp_path/'finish.sqlite');store=app.state.store
    v=Variants(app.state.sync);pid='CN:151C:001'
    variants=v.view(pid)['variants']
    assert {x['finishLabel'] for x in variants}=={'普通版','精灵球版','大师球版'}
    master=next(x['variantId'] for x in variants if x['finishCode']==2)
    ball=next(x['variantId'] for x in variants if x['finishCode']==1)
    store.update_collection([{'printingId':pid,'condition':'全新','quantity':3,'notes':'旧收藏','wishlist':True}],0)
    out=v.set_holding(pid,'全新',master,2,1)
    assert out['entries'][0]['quantity']==5 and out['entries'][0]['unassigned']==3
    v.set_holding(pid,'全新',ball,1,2)
    v.set_holding(pid,'全新',master,0,3)
    record=store.collection()['entries'][0]
    assert record['quantity']==4 and record['notes']=='旧收藏' and record['wishlist']
    v.set_holding(pid,'良好',master,1,4)
    assert len(v.view(pid)['entries'])==2
    with pytest.raises(SyncError,match='收藏已变化'):
        v.set_holding(pid,'全新',master,3,4)
    with pytest.raises(SyncError):v.set_holding(pid,'无效',master,3,5)
    with pytest.raises(SyncError):v.set_holding(pid,'全新','foreign',3,5)
    assert store.collection()['version']==5
