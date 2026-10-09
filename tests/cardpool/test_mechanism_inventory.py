from scripts.cardpool.mechanism_inventory import extract, build
from scripts.cardpool.check_mechanisms import plan
import pytest


def page(text):
    return {
        "revision": 123,
        "text": "==卡牌信息==\n" + text + "\n{{ExpansionList/header}}",
    }


def test_numeric_variants_keep_exact_rules_separate_and_restrictions_intact():
    first, _ = extract(
        page(
            "{{卡牌信息/power|powertype=SV特性|eeffect=Once during your turn, draw 2 cards.|effectZHS=自己的回合可以使用1次。抽2张卡。}}"
        )
    )
    second, _ = extract(
        page(
            "{{卡牌信息/power|powertype=SV特性|eeffect=Once during your turn, draw 3 cards.|effectZHS=自己的回合可以使用1次。抽3张卡。}}"
        )
    )
    limited, _ = extract(
        page(
            "{{卡牌信息/power|powertype=SV特性|eeffect=Once during your turn, if this Pokemon is Active, draw 2 cards.|effectZHS=战斗场上可以使用1次。抽2张卡。}}"
        )
    )
    assert first[0]["exactGroup"] != second[0]["exactGroup"]
    assert first[0]["parameterGroup"] == second[0]["parameterGroup"]
    assert first[0]["parameterGroup"] != limited[0]["parameterGroup"]
    assert set(first[0]["families"]) >= {"ability_lifecycle", "draw_hand"}


def test_unknown_prose_and_multiple_rule_versions_are_not_silently_dropped():
    units, gaps = extract(
        page(
            "特殊胜利规则\n{{训练家卡信息/multimain|eeffect=Draw 2 cards.|eeffect2=Draw 3 cards.}}\n{{UnknownRule|limit=1}}"
        )
    )
    assert "MULTI_VERSION_RULE_REVIEW" in gaps
    assert "NON_TEMPLATE_RULE_TEXT" in gaps
    assert "UNKNOWN_TEMPLATE:UnknownRule" in gaps
    assert any(u["kind"] == "unparsed" for u in units)


def test_every_target_and_unresolved_identity_remains_accounted_for():
    cards = [
        {
            "printingId": f"CN:TEST:{i}",
            "cardPage": "P" if i < 2 else None,
            "mark": "G" if i < 3 else None,
            "releasedAt": "2026-01-01",
            "effectStatus": "unverified",
            "sourceVerified": False,
        }
        for i in range(4)
    ]
    report = build(
        {"version": "fixture", "cards": cards},
        {"effects": []},
        {"P": page("{{卡牌信息/attack|cost=无|damage=20}}")},
    )
    assert report["autoApproval"] is False
    assert report["counts"]["targets"] == 3 and report["counts"]["unsupported"] == 3
    assert report["counts"]["unsupportedPages"] == 1
    assert report["counts"]["unsupportedWithoutPage"] == 1
    assert sum(len(p["printings"]) for p in report["pages"]) == 3
    assert len(report["unresolvedIdentities"]) == 1


def test_incremental_checks_do_not_run_ten_thousand_games_or_overwrite_acceptance():
    targeted = plan("targeted")
    assert len(targeted["commands"]) == 1
    assert not targeted["updatesReleaseAcceptance"]
    integration = plan("integration")
    assert "100" in integration["commands"][1]["args"]
    release = plan("release")
    assert "1000" in release["commands"][1]["args"]
    assert len(release["commands"]) == 4
    for p in (targeted, integration, release):
        assert p["directory"].startswith("artifacts/cardpool/checks/")
        assert not any(
            "ghij-backend.xml" in arg or "accept.py" in arg
            for c in p["commands"]
            for arg in c["args"]
        )


def test_new_family_tests_can_be_included_without_replacing_shared_checks():
    node = "tests/cardpool/test_mechanism_inventory.py::test_every_target_and_unresolved_identity_remains_accounted_for"
    result = plan("targeted", extra_tests=[node])
    assert node in result["commands"][0]["args"]
    assert "tests/simulation/test_prompt_matrix.py" in result["commands"][0]["args"]
    with pytest.raises(ValueError):
        plan("targeted", extra_tests=["scripts/cardpool/accept.py"])


def test_implemented_cards_share_families_without_promoting_pending_aliases():
    cards = [
        {
            "printingId": f"CN:TEST:{i}",
            "cardPage": "P" if i < 2 else None,
            "mark": "G",
            "releasedAt": "2026-01-01",
            "effectStatus": "verified" if i != 1 else "unverified",
            "sourceVerified": i != 1,
            "engineId": "DRAW" if i < 2 else "ENERGY",
        }
        for i in range(3)
    ]
    release = {
        "effects": [
            {
                "effectKey": "DRAW",
                "printings": ["CN:TEST:0", "CN:TEST:1"],
                "mechanisms": ["draw"],
                "implementation": "example.Draw",
                "semantics": {"abilities": [{"name": "Draw"}]},
            },
            {
                "effectKey": "ENERGY",
                "printings": ["CN:TEST:2"],
                "mechanisms": ["basic_energy"],
            },
            {
                "effectKey": "UNKNOWN",
                "printings": ["CN:OUT:1"],
                "mechanisms": ["future_mechanic"],
            },
        ]
    }
    report = build(
        {"version": "fixture", "cards": cards},
        release,
        {"P": page("{{卡牌信息/power|eeffect=Once during your turn, draw 2 cards.}}")},
    )
    assert report["counts"]["supported"] == 2
    assert report["counts"]["classifiedSupportedPrintings"] == 2
    assert report["counts"]["implementedEffects"] == 3
    assert report["counts"]["unmappedImplementedMechanisms"] == ["future_mechanic"]
    row = next(r for r in report["pages"] if r["page"] == "P")
    assert row["supportedPrintings"] == ["CN:TEST:0"]
    assert row["unsupportedPrintings"] == ["CN:TEST:1"]
    assert row["implementedEffectKeys"] == ["DRAW"]
    energy = next(r for r in report["pages"] if r["page"].startswith("identity:"))
    assert energy["families"] == ["energy_move"]
    assert "RULE_BODY_MISSING" in energy["reviewBlockers"]
    draw = next(f for f in report["families"] if f["id"] == "draw_hand")
    assert draw["supportedPrintings"] == draw["printings"] == 1
    assert report["abilityBuckets"][0]["members"][0]["implementedEffectKeys"] == [
        "DRAW"
    ]
    unknown = report["implementedEffects"][-1]
    assert unknown["families"] == ["unclassified"]
    assert unknown["outsideTargetPrintings"] == ["CN:OUT:1"]
