import copy
from packages.cardpool.scope import includes, check
from scripts.cardpool.scope_inventory import build
from packages.collection.domain import CARDS, RAW, validation
from packages.simulation.registry import RELEASE


def test_scope_is_not_official_legality_and_does_not_admit_legacy_or_fairy():
    for mark in "GHIJ":
        assert includes({"mark": mark})
    for mark in (None, "E", "F", "K"):
        assert not includes({"mark": mark, "legacyReprintVerified": True})
    assert includes({"category": "能量", "basicEnergyType": "water"})
    assert not includes({"category": "能量", "basicEnergyType": "fairy"})
    assert not includes({"category": "宝可梦", "basicEnergyType": "water"})
    result = check([{"printingId": "old", "quantity": 1}], {"old": {"mark": "F"}})
    assert result["outside"] == ["old"] and not result["supported"]


def test_scope_denominator_does_not_shrink_to_implemented_cards():
    a = build(RAW, RELEASE)
    unreviewed = copy.deepcopy(RAW)
    for c in unreviewed["cards"]:
        c["sourceVerified"] = False
        c["effectStatus"] = "unverified"
    b = build(unreviewed, RELEASE)
    assert a["counts"]["targets"] > 3400
    assert a["denominatorVersion"] == b["denominatorVersion"]
    assert b["counts"]["supported"] == 0
    assert b["counts"]["unsupported"] == a["counts"]["targets"]
    assert a["counts"]["unresolvedMarks"] == b['counts']['unresolvedMarks']


def test_all_eight_reference_energies_can_be_used_in_custom_revisions():
    basic = next(
        c
        for c in CARDS.values()
        if c["effectStatus"] == "verified" and c["isBasicPokemon"]
    )
    for code in ("037", "038", "039", "040", "041", "042", "043", "044"):
        result = validation(
            [
                {"printingId": basic["printingId"], "quantity": 4},
                {"printingId": "CN:CSM2.1C:" + code, "quantity": 56},
            ]
        )
        assert result["playable"], (code, result)
    assert not validation(
        [
            {"printingId": basic["printingId"], "quantity": 4},
            {"printingId": "CN:CSM2.1C:045", "quantity": 56},
        ]
    )["playable"]


def test_official_legacy_exception_does_not_bypass_application_scope(monkeypatch):
    entries = copy.deepcopy(RAW["templates"][0]["entries"])
    pid = next(e["printingId"] for e in entries if CARDS[e["printingId"]]['cnName'] == '高级球')
    old = {**CARDS[pid], "mark": "F", "legacyReprintVerified": True, "latestEffectRevision": "reviewed-test-fixture"}
    monkeypatch.setitem(CARDS, pid, old)
    result = validation(entries)
    assert result["legality"] == "valid"
    assert not result["playable"]
    assert any(i["code"] == "OUTSIDE_BATTLE_SCOPE" and pid in i["cardRefs"] for i in result["issues"])
