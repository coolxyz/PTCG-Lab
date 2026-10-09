import xml.etree.ElementTree as ET
from scripts.cardpool.close_scoped_acceptance import assess, ENGINE_VERSION, RELEASE_VERSION, SCOPE_VERSION, AI_VERSION


def fixture():
    inventory = {"catalogVersion": "test", "counts": {}, "targets": [{"printingId": "good", "supported": True}, {"printingId": "unclear", "supported": False}]}
    effects = {"effects": [{"effectKey": "P4P-ABC"}]}
    exceptions = {"printingIds": ["unclear"], "status": "pending-source-verification"}
    cases = [ET.Element("testcase", name="test_directed_pause_continuations[P4P-ABC-AttackAction-atomic]")]
    browser = {"stats": {"expected": 25}}
    matches = {"engineVersion": ENGINE_VERSION, "releaseVersion": RELEASE_VERSION, "scopeVersion": SCOPE_VERSION, "catalogVersion": "test", "aiVersion": AI_VERSION,
               "games": 1000, "results": [{"status": "finished"}] * 1000}
    return inventory, effects, exceptions, cases, browser, matches


def test_only_authorized_exceptions_can_close_the_implementation_scope():
    args = fixture()
    result = assess(*args)
    assert result["status"] == "PASS_WITH_SOURCE_EXCEPTIONS"
    assert result["unqualifiedFullP4Status"] == "INCOMPLETE"
    args[0]["targets"].append({"printingId": "engineering-gap", "supported": False})
    assert assess(*args)["status"] == "INCOMPLETE"


def test_missing_directed_effect_failure_or_version_drift_blocks_closure():
    for change in ("effect", "failure", "version", "game"):
        args = fixture()
        if change == "effect":
            args[1]["effects"].append({"effectKey": "untested"})
        elif change == "failure":
            ET.SubElement(args[3][0], "failure")
        elif change == "version":
            args[5]["engineVersion"] = "old"
        else:
            args[5]["results"][-1] = {"status": "error"}
        assert assess(*args)["status"] == "INCOMPLETE"
