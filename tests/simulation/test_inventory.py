from scripts.simulation.inventory import build


def test_inventory_is_complete_for_input_but_not_claimed_complete_for_format():
    report = build()
    assert report["targetCompleteness"] == "INCOMPLETE"
    assert len(report["cards"]) == report["counts"]["printings"]
    assert len({c["printingId"] for c in report["cards"]}) == len(report["cards"])
    for card in report["cards"]:
        if card["status"] == "unverified":
            assert card["effectKey"] is None
            assert "RULE_IDENTITY_UNREVIEWED" in card["gaps"]
    assert all(e["registered"] for e in report["effects"])
    assert any(p["kind"] == "promo" for p in report["products"])
    assert any(p["kind"] == "preconstructed" for p in report["products"])
    assert all(p["status"] != "complete" for p in report["products"])
