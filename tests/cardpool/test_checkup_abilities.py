from types import SimpleNamespace
import pytest
from packages.rules.plain import SPECS
from ptcg.core.enums import Coin, Stage
from ptcg.utils.utils import next_turn
from packages.rules.checkup import finish, needed
from tests.cardpool.test_checkup import board
from test_effects import drive


def aura(card, **rule):
    card.spec = {"abilities": [{"kind": "checkup", **rule}]}


def test_poison_and_burn_bonus_add_to_existing_condition_damage(monkeypatch):
    s, p, o = board()
    aura(p.active[0], activeOnly=True, poisonBonus=50)
    aura(p.bench[0], burnBonus=30)
    target = o.active[0]
    target.hp, target.poisoned, target.burned, target.poison_damage = 300, True, True, 30
    monkeypatch.setattr("packages.rules.checkup.flip_coin", lambda s, *args, **kwargs: Coin.TAIL)
    next_turn(s)
    drive(finish(s))
    assert target.hp == 170 and target.poisoned and target.burned


def test_sand_stream_triggers_without_status_and_only_hits_opponent_basics():
    s, p, o = board()
    aura(p.active[0], activeOnly=True, basicCounters=20)
    o.bench[0].stage = Stage.STAGE_1
    active, bench, own = o.active[0], o.bench[0], p.active[0]
    hp = (active.hp, bench.hp, own.hp)
    assert needed(s)
    next_turn(s)
    drive(finish(s))
    assert (active.hp, bench.hp, own.hp) == (hp[0] - 20, hp[1], hp[2])


def test_suppressed_checkup_ability_does_not_trigger():
    s, p, o = board()
    aura(p.active[0], activeOnly=True, basicCounters=20)
    o.active[0].ability = [SimpleNamespace(suppresses_opponent_active_abilities=True)]
    assert not needed(s)


def test_multiple_froslass_and_poison_resolve_without_confirmation():
    s,p,o=board()
    for holder in (p.bench[0],o.bench[0]):
        holder.name="Froslass"
        aura(holder, abilityCounters=10, exceptName="Froslass")
    target=o.active[0]
    target.ability=[SimpleNamespace(name="Present")]
    target.poisoned=True
    target.poison_damage=10
    before=target.hp
    next_turn(s)
    seen=drive(finish(s))
    assert not seen
    assert target.hp==before-30
    assert s.turn==o.id


def test_two_froslass_do_not_knock_out_ten_hp_dragapult_without_an_ability():
    from packages.rules.effects import Dragapult
    from test_effects import zone

    s, p, o = board()
    target = Dragapult()
    zone(o, "active", [target])
    target.hp = 10
    assert not target.ability
    for holder in (p.bench[0], o.bench[0]):
        holder.name = "Froslass"
        aura(holder, abilityCounters=10, exceptName="Froslass")
    # A Pokemon with a real Ability still receives both counters.
    p.active[0].ability = [SimpleNamespace(name="Present")]
    before = p.active[0].hp
    next_turn(s)
    assert not drive(finish(s))
    assert target in o.active and target.hp == 10
    assert p.active[0].hp == before - 20
    assert len(p.prize) == len(o.prize) == 6


@pytest.mark.parametrize("spec", [s for s in SPECS if s.get("pokemonRule") == "TERA"], ids=lambda s: s["effectKey"])
def test_compiled_tera_checkup_uses_only_printed_abilities(spec):
    from ptcg.core.card_registry import registry
    from packages.rules.core_fixes import shield_damage
    from test_effects import zone

    s, p, o = board()
    target = registry.get(spec["effectKey"])()
    zone(o, "active", [target])
    assert [a.name for a in target.ability] == [a["name"] for a in spec.get("abilities", [])]
    assert all(a.name != "Tera" for a in target.ability)
    for holder in (p.bench[0], o.bench[0]):
        holder.name = "Froslass"
        aura(holder, abilityCounters=10, exceptName="Froslass")
    before = target.hp
    next_turn(s)
    drive(finish(s))
    assert target.hp == before - (20 if spec.get("abilities") else 0)
    # The Tera rule remains effective even when actual Abilities are disabled.
    zone(o, "active", [])
    zone(o, "bench", [target])
    target.ability_blocked_turn = s.turn_number
    assert shield_damage(target, 100, s, p.active[0]) == 0
