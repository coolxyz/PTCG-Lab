from scripts.cardpool.accept import coverage


def test_missing_rule_and_unresolved_denominator_prevent_full_pool_claim():
    inventory = {
        "asOf": "2026-10-01",
        "formatId": "test",
        "complete": False,
        "counts": {"gaps": {"RELEASE_EVIDENCE_REQUIRED": 1}},
        "cards": [
            {
                "printingId": "a",
                "cardPage": "a",
                "releaseStatus": "released",
                "formatStatus": "current-mark",
                "gaps": [],
            },
            {
                "printingId": "b",
                "cardPage": "b",
                "releaseStatus": "released",
                "formatStatus": "current-mark",
                "gaps": ["RULE_EFFECT_UNREVIEWED"],
            },
        ],
    }
    catalog = {
        "cards": [
            {"printingId": "a", "effectStatus": "verified", "sourceVerified": True}
        ]
    }
    release = {"effects": [{"printings": ["a"]}]}
    result = coverage(inventory, catalog, release)
    assert result["supportedCandidates"] == 1
    assert result["unsupportedCandidates"] == 1
    assert not result["effectsComplete"]
    # Even 100% of known rows is insufficient when the denominator is unresolved.
    inventory["cards"].pop()
    result = coverage(inventory, catalog, release)
    assert result["unsupportedCandidates"] == 0
    assert not result["sourceComplete"] and not result["effectsComplete"]
