from scripts.cardpool.accept_ghij import (
    assess,
    ENGINE_VERSION,
    EFFECT_VERSION,
    SCOPE_VERSION,
    AI_VERSION,
)


def test_supported_subset_success_cannot_pass_missing_ghij_targets():
    inventory = {
        "catalogVersion": "catalog",
        "counts": {
            "targets": 1000,
            "unsupported": 999,
            "unresolvedMarks": 0,
            "gaps": {},
        },
    }
    matches = {
        "engineVersion": ENGINE_VERSION,
        "releaseVersion": EFFECT_VERSION,
        "scopeVersion": SCOPE_VERSION,
        "catalogVersion": "catalog",
        "aiVersion": AI_VERSION,
        "games": 10000,
        "results": [{"status": "finished"}] * 10000,
        "statuses": {"finished": 10000},
        "actionCounts": {"one:AttackAction": 1},
    }
    result = assess(
        inventory,
        matches,
        {"tests": 1},
        {"expected": 1},
        {"effects": [{"effectKey": "one"}]},
        {},
    )
    assert result["scopedChecks"]["publishedSubsetA1"]["passed"]
    assert result["status"] == "INCOMPLETE"
    assert not result["fullP4Gates"]["P4.1_allGHIJEffects"]
    assert not result["fullP4Gates"]["P4.6_fullTargetStability"]
    matches["engineVersion"] = "old"
    assert not assess(
        inventory, matches, {"tests": 1}, {"expected": 1}, {"effects": []}, {}
    )["scopedChecks"]["publishedSubsetA1"]["passed"]
