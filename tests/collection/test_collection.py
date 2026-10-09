import copy, math
import pytest
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import (
    CARDS,
    RAW,
    DomainError,
    analysis,
    validation,
    normalize,
    parse_import,
    export_csv,
    missing,
    catalog_view,
    content_hash,
)
from packages.collection.store import Store

P = "CN:CSVM2cC:007"
ENERGY = "CN:CSM2.1C:044"
T = RAW["templates"][0]["entries"]
BASIC = next(p for p, c in CARDS.items() if c["isBasicPokemon"])
SAME = next(
    [p for p, c in CARDS.items() if c["definitionId"] == d]
    for d in sorted({c["definitionId"] for c in CARDS.values() if not c.get('basicEnergyType')})
    if sum(c["definitionId"] == d for c in CARDS.values()) > 1
)


def entry(p=P, q=1):
    return {"printingId": p, "quantity": q}


def hold(p=P, q=1, **kw):
    return {**entry(p, q), "condition": "未标注", "notes": "", "wishlist": False, **kw}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "db.sqlite")


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "api.sqlite"))


def test_catalog_scope_and_hash():
    assert len(CARDS) >= 12000
    assert sum(c["effectStatus"] == "verified" for c in CARDS.values()) >= 164
    assert {"CN:CSM2.1C:044", "CN:CSM2.1C:038", "CN:CSM2.1C:041"} <= CARDS.keys()
    assert len({c.get('basicEnergyType') for c in CARDS.values() if c.get('basicEnergyType') and c['effectStatus']=='verified'}) == 8
    assert P in {c["printingId"] for c in catalog_view("Gholdengo")}
    assert catalog_view("CSVM2cC 007")[0]["printingId"] == P
    assert all(c["category"] == "能量" for c in catalog_view(category="能量"))
    assert RAW["version"] == content_hash(
        {k: v for k, v in RAW.items() if k != "version"}
    )


@pytest.mark.parametrize(
    "entries",
    [
        [entry(q=True)],
        [entry(q=0)],
        [entry(q=61)],
        [entry("FAKE")],
        [{**entry(), "engineId": "HACK"}],
        [entry(q=40), entry(q=30)],
    ],
)
def test_bad_entries(entries):
    with pytest.raises(DomainError):
        normalize(entries)


@pytest.mark.parametrize("template", RAW["templates"])
def test_verified_templates(template):
    v = validation(template["entries"])
    assert v["legality"] == "valid" and v["playable"] and v["engine"] == "verified"


def test_delete_deck_conflicts_and_preserves_revisions(client):
    d = client.post("/api/decks", json={"name": "删除测试", "entries": T}).json()
    url = "/api/decks/" + d["id"]
    revision = client.post(url + "/revisions", json={"expectedVersion": 1}).json()
    updated = client.put(url, json={"name": "已更新", "entries": T, "expectedVersion": 1})
    assert updated.status_code == 200
    assert client.request("DELETE", url, json={"expectedVersion": 1}).status_code == 409
    assert client.get(url).status_code == 200
    assert client.request("DELETE", url, json={"expectedVersion": 2}).status_code == 200
    assert client.get(url).status_code == 404
    assert all(x["id"] != d["id"] for x in client.get("/api/decks").json())
    with client.app.state.store.db() as db:
        assert db.execute("SELECT hash FROM revisions WHERE id=?", (revision["id"],)).fetchone()[0] == revision["hash"]
    assert client.request("DELETE", url, json={"expectedVersion": 2}).status_code == 404
    assert client.put(url, json={"name": "过期保存", "entries": T, "expectedVersion": 2}).status_code == 404


def test_custom_legal_experimental_composition():
    v = validation([entry(BASIC, 1), entry(ENERGY, 59)])
    assert v["legality"] == "valid" and v["playable"]
    assert v["executionSupport"] == "experimental"
    assert "EXPERIMENTAL_COMPOSITION" in [i["code"] for i in v["issues"]]


def test_same_name_across_printings_and_ace():
    v = validation(
        [entry(SAME[0], 3), entry(SAME[1], 2), entry(BASIC), entry(ENERGY, 54)]
    )
    issue = next(i for i in v["issues"] if i["code"] == "SAME_NAME_LIMIT")
    assert set(SAME[:2]) <= set(issue["cardRefs"]) and issue["suggestedFix"]
    aces = [p for p, c in CARDS.items() if c["aceSpec"]]
    assert "ACE_SPEC_LIMIT" in [
        i["code"]
        for i in validation(
            [entry(aces[0]), entry(aces[1]), entry(BASIC), entry(ENERGY, 57)]
        )["issues"]
    ]


def test_draft_persistence_conflict_and_immutable_snapshot(store):
    d = store.create_deck("测试", T)
    r = store.freeze(d["id"], 1)
    assert store.freeze(d["id"], 1)["id"] == r["id"]
    assert r["hash"] == content_hash(
        {k: v for k, v in r.items() if k not in ("id", "number", "hash", "createdAt")}
    )
    store.save_deck(d["id"], "修改", [], 1)
    assert Store(store.path).deck(d["id"])["entries"] == []
    assert store.revisions(d["id"])[0] == r
    with pytest.raises(DomainError, match=""):
        store.save_deck(d["id"], "覆盖", T, 1)
    with pytest.raises(DomainError) as err:
        store.freeze(d["id"], 2)
    assert err.value.code == "DECK_INVALID"
    with pytest.raises(DomainError) as err:
        store.freeze(d["id"], 1)
    assert err.value.status == 409


def test_ambiguous_import_and_explicit_identity():
    name = CARDS[SAME[0]]["cnName"]
    p = parse_import("2 " + name)
    assert not p["ready"] and len(p["rows"][0]["candidates"]) >= 2
    assert parse_import("2 " + name, resolutions={"0": SAME[1]})["entries"] == [
        entry(SAME[1], 2)
    ]
    with pytest.raises(DomainError):
        parse_import("2 " + name, resolutions={"0": ENERGY})
    assert parse_import("4 赛富豪ex CSVM2cC 007")["entries"] == [entry(P, 4)]
    assert not parse_import("printingId,quantity\nFAKE,1")["ready"]
    assert not parse_import(f"60 {ENERGY}\n1 {ENERGY}")["ready"]


def test_csv_roundtrip_zero_wishlist_notes():
    rows = [
        hold(q=0, wishlist=True, notes='=HYPERLINK("x")\n第二行'),
        hold(ENERGY, 9999, condition="良好", notes=" 有空白 "),
    ]
    text = export_csv(rows, "collection")
    assert "'=HYPERLINK" in text
    parsed = parse_import(text, "collection")
    assert parsed["ready"]
    assert parsed["entries"] == rows
    assert not parse_import(
        "printingId,quantity,wishlist\n" + P + ",1,maybe", "collection"
    )["ready"]


@pytest.mark.parametrize("qty", ["-1", "1.1", "x", "10000", ""])
def test_invalid_collection_quantities(qty):
    assert not parse_import(f"printingId,quantity\n{P},{qty}", "collection")["ready"]


def test_duplicate_rows_merge_and_atomic_rejection(store):
    text = f"2 {P}\n3 {P}"
    p = parse_import(text, "collection")
    assert p["ready"] and p["entries"][0]["quantity"] == 5 and p["warnings"]
    with pytest.raises(DomainError):
        store.import_collection(text + "\n1 unknown", {}, 0)
    assert store.collection() == {"version": 0, "entries": []}


def test_import_idempotency_undo_preserves_later_changes(store):
    text = export_csv([hold(q=2, wishlist=True, notes="来自导入")], "collection")
    b = store.import_collection(text, {}, 0)
    assert store.import_collection(text, {}, 0)["duplicate"]
    assert store.collection()["entries"][0]["wishlist"]
    store.update_collection([hold(q=5, wishlist=True, notes="后续修改")], 1)
    store.undo_import(b["id"], 2)
    assert store.collection()["entries"][0] == hold(q=3, notes="后续修改")
    assert store.undo_import(b["id"], 0)["duplicate"]
    with pytest.raises(DomainError):
        store.import_collection(text, {}, 3)


def test_undo_conflict_is_atomic(store):
    b = store.import_collection(f"2 {P}\n2 {ENERGY}", {}, 0)
    store.update_collection([hold(ENERGY, 1)], 1)
    before = store.collection()
    with pytest.raises(DomainError):
        store.undo_import(b["id"], 2)
    assert store.collection() == before and store.batches()[0]["status"] == "applied"


def test_collection_version_conditions_restart(store):
    store.update_collection([hold(q=2), hold(q=3, condition="良好")], 0)
    with pytest.raises(DomainError):
        store.update_collection([hold(q=9)], 0)
    assert sum(e["quantity"] for e in Store(store.path).collection()["entries"]) == 5
    with pytest.raises(DomainError):
        store.update_collection([hold(q=4), hold("fake")], 1)
    assert store.collection()["entries"][0]["quantity"] in (2, 3)


def test_equivalent_inventory_allocated_once():
    es = [entry(SAME[0], 2), entry(SAME[1], 2)]
    co = [hold(SAME[0], 3)]
    assert missing(es, co, "exact")["totalMissing"] == 2
    equivalent = missing(es, co, "equivalent")
    assert equivalent["totalMissing"] == 1 and equivalent["ownedUsed"] == 3


def test_seeded_opening_probability():
    es = [entry(BASIC, 4), entry(ENERGY, 56)]
    a = analysis(es, 42)
    assert a == analysis(list(reversed(es)), 42)
    assert len(a["openingHand"]) == 7
    assert a["openingBasicProbability"] == pytest.approx(
        1 - math.comb(56, 7) / math.comb(60, 7)
    )
    assert analysis([])["openingBasicProbability"] is None
    assert analysis([entry(ENERGY, 60)])["openingBasicProbability"] == 0


def test_api_strict_input_and_origin(client):
    assert client.get("/api/cards").status_code == 200
    assert client.get("/api/cards", headers={"host": "evil.test"}).status_code == 403
    assert (
        client.post(
            "/api/decks",
            json={"name": "x", "entries": []},
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    for e in [entry(q=True), entry(q="1"), {**entry(), "effectStatus": "verified"}]:
        assert (
            client.post("/api/decks", json={"name": "x", "entries": [e]}).status_code
            == 422
        )
    assert (
        client.post(
            "/api/decks",
            json={"name": "x", "entries": []},
            headers={"origin": "http://testserver"},
        ).status_code
        == 201
    )


def test_api_user_flow(client):
    meta = client.get("/api/meta").json()
    es = meta["templates"][0]["entries"]
    d = client.post("/api/decks", json={"name": "我的卡组", "entries": es}).json()
    r = client.post(f"/api/decks/{d['id']}/revisions", json={"expectedVersion": 1})
    assert r.status_code == 200 and r.json()["catalogVersion"] == meta["catalogVersion"]
    csv = client.get(f"/api/decks/{d['id']}/export").text
    assert normalize(parse_import(csv)["entries"]) == normalize(es)
    assert (
        client.put(
            "/api/collection", json={"expectedVersion": 0, "changes": [hold(q=4)]}
        ).status_code
        == 200
    )
    assert (
        client.get("/api/cards", params={"owned": "owned"}).json()["cards"][0]["owned"]
        == 4
    )
    assert client.get("/api/collection").headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "notes", ["'=x", "'", "''x", "@formula", "-1", " plain ", "line1\nline2"]
)
def test_notes_csv_escape_roundtrip(notes):
    assert (
        parse_import(export_csv([hold(notes=notes)], "collection"), "collection")[
            "entries"
        ][0]["notes"]
        == notes
    )


def test_malformed_csv_and_huge_quantity():
    for text in [
        "printingId,quantity,quantity\n" + P + ",1,2",
        "printingId,quantity\n" + P + ",1,extra",
    ]:
        with pytest.raises(DomainError):
            parse_import(text, "collection")
    assert not parse_import("9" * 6000 + " " + P, "collection")["ready"]


def test_batch_fingerprint_independent_of_row_order(store):
    rows = [hold(q=1, notes="B"), hold(q=2, notes="A", wishlist=True)]
    first = store.import_collection(export_csv(rows, "collection"), {}, 0)
    repeat = store.import_collection(
        export_csv(list(reversed(rows)), "collection"), {}, 0
    )
    assert repeat["duplicate"] and repeat["id"] == first["id"]
    assert store.collection()["entries"][0]["notes"] == "A；B"


def test_pack_filter_distinguishes_shared_series(client):
    packs = {p["name"]: p["id"] for p in RAW["products"]}
    first, second = packs["收集啦151 旅"], packs["收集啦151 望"]

    def get(pack, **filters):
        response = client.get("/api/cards", params={"pack": pack, **filters})
        assert response.status_code == 200
        return response.json()["cards"]

    a, b = get(first), get(second)
    assert len(a) == len(b) == 186
    assert {c["printingId"] for c in a} != {c["printingId"] for c in b}
    assert all(first in c["productIds"] for c in a)
    assert all(second in c['productIds'] for c in b)
    assert get(first, q="CN:151C:001")[0]["cnName"] == "妙蛙种子"
    assert all(c["category"] == "训练家" for c in get(first, category="训练家"))
    assert get("unknown-pack") == []


def test_revision_identity_depends_on_cards_not_release(store, monkeypatch):
    import packages.collection.store as module
    deck = store.create_deck("稳定版本", T)
    first = store.freeze(deck["id"], 1)
    monkeypatch.setattr(module, "CATALOG_VERSION", "updated")
    renamed = store.save_deck(deck["id"], "改名", list(reversed(T)), 1)
    assert store.freeze(deck["id"], renamed["version"])["id"] == first["id"]
    changed = [dict(e) for e in T]
    # Replace a single copy with basic energy while preserving 60 cards.
    energy = next(e for e in changed if module.CARDS[e["printingId"]].get("basicEnergyType"))
    other = next(e for e in changed if e is not energy and e["quantity"] > 1)
    other["quantity"] -= 1
    energy["quantity"] += 1
    draft = store.save_deck(deck["id"], "改卡表", changed, renamed["version"])
    second = store.freeze(deck["id"], draft["version"])
    assert second["id"] != first["id"] and second["number"] == 2
    assert store.revisions(deck["id"])[1] == first
