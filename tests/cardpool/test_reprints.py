import copy
import hashlib
import json

from scripts.cardpool.reprints import ROOT, propose, read
from packages.simulation.registry import additive_release, compatible_release, RELEASE


def test_reprint_review_requires_exact_identity_and_blocks_conflicts():
    # Test the historical importer with an explicit source fixture, rather
    # than relying on identities removed from the production catalogue.
    anchor={'printingId':'CN:test:001','engineId':RELEASE['effects'][0]['effectKey'],'sourceVerified':True,'effectStatus':'verified'}
    baseline={'cards':[anchor]}
    catalog={'cards':[anchor], 'products':[]}
    base={'cardPage':'same-rule-page','gaps':[], 'releaseStatus':'released','formatStatus':'current-mark','facts':{'hp':60},'cardSource':{'sourceUrl':'https://example.test/card','revision':1},'collectorNumber':'001','productCode':'test','cnName':'fixture','mark':'G','sources':[{'releaseBound':'2026-01-01','product':'fixture','source':{'title':'fixture','revision':1,'pageId':1}}]}
    inventory={'asOf':'2026-10-07','cards':[{**base,'printingId':anchor['printingId']},{**copy.deepcopy(base),'printingId':'CN:test:002'}]}
    original=copy.deepcopy(RELEASE)
    original['effects'][0]['printings'].append(anchor['printingId'])
    result, release, reviews = propose(inventory, catalog, baseline, original)
    assert len(reviews) == 1
    assert additive_release(original, release)
    pid = reviews[0]["printingId"]
    source = next(c for c in inventory["cards"] if c["printingId"] == pid)
    source["gaps"].append("PRINTING_IDENTITY_CONFLICT")
    _, _, rejected = propose(inventory, catalog, baseline, original)
    assert pid not in {r["printingId"] for r in rejected}
    source["gaps"].remove("PRINTING_IDENTITY_CONFLICT")
    source["cardPage"] = "same name, different rules"
    _, _, rejected = propose(inventory, catalog, baseline, original)
    assert pid not in {r["printingId"] for r in rejected}


def test_compatibility_rejects_changed_semantics_removed_alias_and_engine():
    new = copy.deepcopy(RELEASE)
    new["effects"][0]["printings"].append("test:new-printing")
    assert additive_release(RELEASE, new)
    new["effects"][0]["semantics"]["hp"] = -1
    assert not additive_release(RELEASE, new)
    new = copy.deepcopy(RELEASE)
    next(row for row in new["effects"] if row["printings"])["printings"].pop()
    assert not additive_release(RELEASE, new)
    new = copy.deepcopy(RELEASE)
    new["engineVersion"] = "different-engine"
    assert not additive_release(RELEASE, new)
    assert not compatible_release("../effects")
    assert not compatible_release("0" * 64)


def test_archived_release_is_hash_pinned_and_additive():
    for path in (ROOT / "data/cardpool/release-history").glob("*.json"):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.stem
        expected = additive_release(json.loads(path.read_bytes()), RELEASE)
        assert compatible_release(path.stem) == expected
        if json.loads(path.read_bytes())["engineVersion"] != RELEASE["engineVersion"]:
            assert not expected
