"""Official SVOM 006/021 HP must survive compilation and publication."""

from copy import deepcopy

from packages.battle import runtime  # Load the checked engine overlay.
from packages.collection.domain import CARDS
from packages.rules.plain import SPECS
from ptcg.core.card_registry import registry
from scripts.cardpool.compile_plain import compile_rule
from tests.wiki_fixtures import load_articles
from scripts.cardpool.source_corrections import apply


def test_marnies_morgrem_official_hp_in_compiler_catalog_and_engine():
    page = load_articles()["瑪俐的詐唬魔（SVOM）"]
    compiled = compile_rule(page)
    assert compiled["hp"] == 100
    assert compiled["attacks"][0]["damage"] == 60
    spec = next(s for s in SPECS if s["effectKey"] == "P4P-2C7FC084F5E6")
    assert spec["hp"] == 100
    for pid in spec["printings"]:
        if pid in CARDS:
            assert CARDS[pid]["hp"] == 100
    card = registry.get(spec["effectKey"])()
    assert card.hp == 100
    assert runtime.Adapter._card(card)["hp"] == 100

    # A later source revision must not inherit this repair silently.
    changed = deepcopy(page)
    changed["revision"] += 1
    fields = {"hp": "70"}
    assert apply(changed, "header", fields) == []
    assert fields["hp"] == "70"
