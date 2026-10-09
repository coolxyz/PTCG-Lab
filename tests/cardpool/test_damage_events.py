"""Attack damage timing examples derived from the official rules Q&A."""

import pytest
from ptcg.core.card_registry import registry
from ptcg.utils.utils import switch_pokemon
from packages.rules.damage_events import deal, finish, coin_armor
from packages.rules.maximum_hp import maximum
from tests.cardpool.test_field_effects import field_fixture
from test_effects import zone, drive


def fixture(rule):
    s, p, o, c, t, a = field_fixture({"kind": "recover_status"})
    c.spec["attacks"][0]["damage"] = 10
    t.spec = {"abilities": [{"trigger": "passive", **rule}]}
    t.weakness = t.resistance = []
    zone(o, "bench", [registry.get("P01-005")()])
    return s, p, o, c, t, a


def test_briar_records_ordinary_defender_without_reaction_ability():
    from ptcg.core.enums import PokemonRule
    s,p,o,c,t,a=fixture({"kind":"continuous"})
    t.spec={"abilities":[]}
    c.pokemonRule=PokemonRule.TERA
    p.tera_bonus_prize_turn=s.turn_number
    deal(c,t,1000,s)
    finish(s)
    assert t.knockout_bonus_prizes==1


def test_equal_damage_retaliation_uses_actual_damage_including_overkill():
    s,p,o,c,t,a=fixture({"kind":"retaliate","equalDamage":True})
    before=c.hp
    t.hp=10
    deal(c,t,100,s)
    finish(s)
    assert c.hp==before-100


@pytest.mark.parametrize("lethal", [False, True])
def test_poison_resolves_after_attacker_cures_itself_even_when_defender_faints(lethal):
    s, p, o, c, t, a = fixture({"kind": "retaliate", "status": "POISONED"})
    a.attack.damage = 10 if not lethal else 1000
    c.poisoned = True
    drive(c.reduce_action(a, s))
    # Next turn defers checkup; poison still exists after the curing attack effect.
    assert c.poisoned and c.poison_damage == 10
    assert (t in o.discard) == lethal


def test_defender_forced_to_bench_still_retaliates_but_attacker_on_bench_not_poisoned():
    s, p, o, c, t, a = fixture({"kind": "retaliate", "status": "POISONED"})
    deal(c, t, 10, s)
    switch_pokemon(t, o.bench[0], o)
    finish(s)
    assert c.poisoned
    del c.poisoned
    switch_pokemon(t, o.active[0], o)
    deal(c, t, 10, s)
    zone(p, "bench", [registry.get("P01-005")()])
    switch_pokemon(c, p.bench[0], p)
    finish(s)
    assert not getattr(c, "poisoned", False)
    assert not getattr(p.active[0], "poisoned", False)


@pytest.mark.parametrize(
    "amount,full,survives", [(1000, True, True), (1000, False, False), (0, True, False)]
)
def test_survival_only_full_hp_lethal_damage(amount, full, survives):
    s, p, o, c, t, a = fixture({"kind": "survive_damage", "remainingHP": 10})
    t.hp = maximum(t) - (0 if full else 10)
    deal(c, t, amount, s)
    if not amount:
        t.hp = 0  # Damage counters or an instant KO have no damage event.
    finish(s)
    assert (t.hp == 10) == survives


def test_survival_uses_final_suppression_and_clears_extra_prize_marker():
    s, p, o, c, t, a = fixture({"kind": "survive_damage", "remainingHP": 10})
    t.hp = maximum(t)
    t.knockout_bonus_prizes = 1
    deal(c, t, 1000, s)
    finish(s)
    assert t.hp == 10 and not hasattr(t, "knockout_bonus_prizes")
    t.hp = maximum(t)
    deal(c, t, 1000, s)
    # Flutter Mane suppresses the opponent Active's abilities at resolution.
    from types import SimpleNamespace

    c.ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    finish(s)
    assert t.hp <= 0


def test_family_retaliation_counts_fainting_defender_and_hits_switched_attacker():
    s, p, o, c, t, a = fixture(
        {
            "kind": "retaliate",
            "counterPerFamily": 30,
            "names": ["Maushold ex", "Tandemaus"],
        }
    )
    t.name, o.bench[0].name = "Maushold ex", "Tandemaus"
    initial = c.hp
    deal(c, t, 1000, s)
    zone(p, "bench", [registry.get("P01-005")()])
    switch_pokemon(c, p.bench[0], p)
    finish(s)
    assert c.hp == initial - 60


def test_prize_reduction_captured_before_field_is_discarded():
    s, p, o, c, t, a = fixture(
        {"kind": "prize_reduction", "count": 1, "requiresName": "Pecharunt ex"}
    )
    t.prize = 2
    o.bench[0].name = "Pecharunt ex"
    a.attack.damage = 1000
    drive(c.reduce_action(a, s))
    assert t in o.discard and len(p.prize) == 5


def test_coin_armor_rolls_only_for_positive_damage(monkeypatch):
    from ptcg.core.enums import Coin

    s, p, o, c, t, a = fixture({"kind": "coin_armor"})
    rolls = []
    monkeypatch.setattr(
        "ptcg.utils.utils.flip_coin", lambda state, *args, **kwargs: rolls.append(1) or Coin.HEAD
    )
    assert coin_armor(t, 0, s) == 0 and not rolls
    assert coin_armor(t, 30, s) == 0 and rolls == [1]


@pytest.mark.parametrize("heads", [False, True])
def test_guts_survival_at_damaged_hp_also_works_for_recoil(heads, monkeypatch):
    from ptcg.core.enums import Coin
    s, p, o, c, t, a = fixture({"kind":"survive_damage", "remainingHP":10, "anyHP":True, "coin":True})
    t.hp = 20
    monkeypatch.setattr("ptcg.utils.utils.flip_coin", lambda state, *args, **kwargs: Coin.HEAD if heads else Coin.TAIL)
    deal(t, t, 30, s)
    finish(s)
    assert t.hp == (10 if heads else -10)


def test_knockout_coin_retaliation_requires_damage_and_original_active_position(monkeypatch):
    from ptcg.core.enums import Coin
    s, p, o, c, t, a = fixture({"kind":"knockout_retaliate", "coin":True, "knockout":True, "activeOnly":True})
    monkeypatch.setattr("ptcg.utils.utils.flip_coin", lambda state, *args, **kwargs: Coin.HEAD)
    before = c.hp
    t.hp = 0
    finish(s)
    assert c.hp == before
    t.hp = 10
    deal(c, t, 20, s)
    switch_pokemon(t, o.bench[0], o)
    finish(s)
    assert c.hp == 0


def test_conditional_coin_armor_does_not_roll_without_confusion(monkeypatch):
    from ptcg.core.enums import Coin, SpecialCondition
    s, p, o, c, t, a = fixture({"kind":"coin_armor", "status":"CONFUSED"})
    rolls = []
    monkeypatch.setattr("ptcg.utils.utils.flip_coin", lambda state, *args, **kwargs: rolls.append(1) or Coin.HEAD)
    assert coin_armor(t, 30, s) == 30 and not rolls
    t.special_condition = SpecialCondition.CONFUSED
    assert coin_armor(t, 30, s) == 0 and rolls == [1]
