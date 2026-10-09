from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, SpecialCondition
from packages.rules.plain import SPECS
from packages.rules.modifiers import refresh_costs, armor
from packages.rules.maximum_hp import reconcile
from packages.rules.protection import blocked
from tests.cardpool.test_checkup import board
from test_effects import zone, drive


def pokemon(text):
    return registry.get(next(s["effectKey"] for s in SPECS if any(a["text"] == text for a in s.get("abilities", []))))()


def test_conditional_attack_free_recomputes_on_teammate_departure_and_suppression():
    s, p, o = board()
    c = pokemon('如果自己的场上有「尼多后」的话，则这只宝可梦的使用招式所需能量，全部消除。')
    zone(p, "active", [c])
    printed = list(c.attacks[0].cost)
    assert printed
    p.bench[0].name = "Nidoqueen"
    refresh_costs(c, s)
    assert not c.attacks[0].cost
    c.ability_blocked_turn = s.turn_number
    refresh_costs(c, s)
    assert c.attacks[0].cost == printed
    del c.ability_blocked_turn
    p.bench = []
    refresh_costs(c, s)
    assert c.attacks[0].cost == printed


def test_fighting_fire_protection_and_burn_immunity_are_independent():
    s, p, o = board()
    c = pokemon("这只宝可梦，不会受到对手{{e|火}}宝可梦的招式的伤害，也不会陷入'''{{TCG|灼伤}}'''状态。")
    zone(o, "active", [c])
    c.burned, c.poisoned = True, True
    reconcile(s)
    assert not getattr(c, "burned", False) and c.poisoned
    p.active[0].cardType = CardType.FIRE
    assert blocked(c, s, "damage", p.active[0])
    assert not blocked(c, s, "effects", p.active[0])
    p.active[0].cardType = CardType.WATER
    assert not blocked(c, s, "damage", p.active[0])
    c.ability_blocked_turn = s.turn_number
    c.burned = True
    reconcile(s)
    assert c.burned


def test_bench_protection_requires_holder_active_and_does_not_protect_active():
    s, p, o = board()
    c = pokemon('只要这只宝可梦在战斗场上，自己所有的备战宝可梦，不会受到对手招式的伤害。')
    zone(o, "active", [c])
    assert blocked(o.bench[0], s, "damage", p.active[0])
    assert not blocked(c, s, "damage", p.active[0])
    from ptcg.utils.utils import switch_pokemon
    switch_pokemon(c, o.bench[0], o)
    assert not blocked(c, s, "damage", p.active[0])


def test_full_hp_armor_stops_after_counters_without_consuming_it():
    s, p, o = board()
    c = pokemon('如果这只宝可梦的HP为全满状态的话，则这只宝可梦受到对手宝可梦的招式的伤害「-80」。')
    zone(o, "active", [c])
    assert armor(c, s, p.active[0]) == 80
    c.hp -= 10
    assert armor(c, s, p.active[0]) == 0


def test_top_window_attachment_discards_remainder_without_touching_tail():
    from packages.rules.entry_effects import effect
    s, p, o = board()
    energy = registry.get("SVE-002")()
    rest = [registry.get("P01-005")() for _ in range(2)]
    tail = registry.get("SVE-008")()
    zone(p, "left", [energy, *rest, tail])
    drive(effect(p.active[0], {"kind":"attach", "origin":"top", "top":3, "count":3, "rest":"discard"}, s))
    assert p.left == [tail] and all(c in p.discard for c in rest)
    assert any(energy in c.attachment for c in p.active + p.bench)


def test_metang_returns_only_unattached_window_to_bottom_and_keeps_tail_order():
    from packages.rules.entry_effects import effect
    s, p, o = board()
    energy = registry.get("SVE-008")()
    rest = [registry.get("SVE-002")() for _ in range(3)]
    tail = [registry.get("SVE-005")() for _ in range(2)]
    zone(p, "left", [energy, *rest, *tail])
    drive(effect(p.active[0], {"kind":"attach", "origin":"top", "top":4, "count":4, "basic":True, "type":"METAL", "rest":"bottom"}, s))
    assert p.left[:2] == tail and set(p.left[2:]) == set(rest)
    assert not p.discard and any(energy in c.attachment for c in p.active + p.bench)


def test_partial_heal_only_affects_active_evolution():
    from packages.rules.entry_effects import effect
    from ptcg.core.enums import Stage
    s, p, o = board()
    c = p.active[0]
    c.hp -= 50
    before = c.hp
    r = {"kind":"heal", "target":"active", "stage":"evolved", "amount":20}
    c.stage = Stage.BASIC
    drive(effect(p.bench[0], r, s))
    assert c.hp == before
    c.stage = Stage.STAGE_1
    drive(effect(p.bench[0], r, s))
    assert c.hp == before + 20
