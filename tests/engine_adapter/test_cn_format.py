"""Synthetic metadata test cases, NOT real certified Chinese card printings."""
import copy
import pytest
from packages.engine_adapter.cn_format import validate


def card(**values):
    return dict(printingId="SYNTHETIC-TEST-ONLY", region="CN", language="zh-Hans", sourceVerified=True,
                releasedAt="2026-01-01", mark="G", nameLimitKey="测试基础", effectStatus="verified", **values)


def deck():
    return [{"quantity": 4, "printing": card(isBasicPokemon=True)},
            {"quantity": 56, "printing": card(basicEnergyType="metal")}]


@pytest.mark.parametrize("mark", ["G", "H", "I", "J"])
def test_current_cn_marks(mark):
    rows = deck()
    rows[0]["printing"]["mark"] = mark
    assert validate(rows)["playable"]


def test_rotation_and_old_snapshot():
    rows = deck()
    rows[0]["printing"]["mark"] = "F"
    assert validate(rows)["legality"] == "invalid"
    assert validate(deck(), "2026-09-15")["legality"] == "invalid"


def test_english_g_mark_is_not_chinese_printing():
    rows = deck()
    rows[0]["printing"].update(region="INTL", language="en")
    assert validate(rows)["legality"] == "invalid"


def test_unknown_data_never_silently_passes():
    rows = deck()
    rows[0]["printing"]["sourceVerified"] = False
    assert validate(rows)["legality"] == "unknown"
    assert not validate(rows)["playable"]


def test_legal_but_unimplemented():
    rows = deck()
    rows[0]["printing"]["effectStatus"] = "unimplemented"
    assert validate(rows)["legality"] == "valid"
    assert not validate(rows)["playable"]


def test_same_name_across_printings():
    rows = deck()
    rows[0]["quantity"] = 3
    another = copy.deepcopy(rows[0])
    another["quantity"] = 2
    another["printing"]["printingId"] = "OTHER-SYNTHETIC"
    rows.append(another)
    rows[1]["quantity"] = 55
    assert "SAME_NAME_LIMIT" in [i["code"] for i in validate(rows)["issues"]]


def test_multiple_different_ace_specs_rejected():
    rows = deck()
    rows[1]["quantity"] = 54
    for name in ("测试王牌甲", "测试王牌乙"):
        c = card(aceSpec=True)
        c["nameLimitKey"] = name
        rows.append({"quantity": 1, "printing": c})
    assert "ACE_SPEC_LIMIT" in [i["code"] for i in validate(rows)["issues"]]


def test_reprint_requires_review_not_just_name():
    rows = deck()
    c = card()
    c.update(nameLimitKey="高级球", mark="F")
    rows[1]["quantity"] -= 1
    rows.append({"quantity": 1, "printing": c})
    assert validate(rows)["legality"] == "invalid"
    c.update(legacyReprintVerified=True, latestEffectRevision="TEST-REVIEW")
    assert validate(rows)["legality"] == "valid"


def test_quantity_and_release_checks():
    rows = deck()
    rows[0]["quantity"] = True
    assert validate(rows)["legality"] == "invalid"
    rows = deck()
    rows[0]["printing"]["releasedAt"] = "2026-10-01"
    assert validate(rows)["legality"] == "invalid"
