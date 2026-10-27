import copy

from scripts.cardpool.reprints import propose
from packages.simulation.registry import RELEASE


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
    assert len(release["effects"][0]["printings"]) == len(original["effects"][0]["printings"]) + 1
    pid = reviews[0]["printingId"]
    source = next(c for c in inventory["cards"] if c["printingId"] == pid)
    source["gaps"].append("PRINTING_IDENTITY_CONFLICT")
    _, _, rejected = propose(inventory, catalog, baseline, original)
    assert pid not in {r["printingId"] for r in rejected}
    source["gaps"].remove("PRINTING_IDENTITY_CONFLICT")
    source["cardPage"] = "same name, different rules"
    _, _, rejected = propose(inventory, catalog, baseline, original)
    assert pid not in {r["printingId"] for r in rejected}


def test_historical_effect_alias_loader_has_been_removed():
    from packages.simulation import registry

    assert not hasattr(registry, "compatible_release")
    assert not hasattr(registry, "additive_release")
