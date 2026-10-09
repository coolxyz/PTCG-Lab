from types import SimpleNamespace
from ptcg.core.action import AttackAction, EvolvePokemonAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import SpecialCondition, CardType
from ptcg.core.reducer import reduce_evolve_pokemon_action
from packages.rules.plain import SPECS, TRAINER_SPECS
from packages.rules.engine import RulesEngine
from tests.cardpool.test_checkup import board
from test_effects import drive, zone


def with_ability(kind):
    spec = next(s for s in SPECS if any(r["kind"] == kind for r in s.get("abilities", [])))
    return registry.get(spec["effectKey"])()


def test_replacement_preserves_damage_energy_status_and_turn_history():
    from packages.rules.pokemon_replacement import replace
    s, p, o = board()
    old = p.active[0]
    new = registry.get("TWM-128")()
    old.hp -= 20
    old.special_condition = SpecialCondition.CONFUSED
    old.attack_locks = {"Test": s.turn_number + 2}
    old.firstTurnPlayed = False
    energy = registry.get("SVE-008")()
    old.attachment = [energy]
    zone(p, "left", [new])
    replace(old, new, p, s)
    assert new.hp == type(new)().hp - 20
    assert new.attachment == [energy] and new.special_condition == SpecialCondition.CONFUSED
    assert new.attack_locks == {"Test": s.turn_number + 2} and not new.firstTurnPlayed
    assert old in p.left and not old.attachment and p.active == [new]


def test_hero_spirit_prevents_ordinary_evolution():
    s, p, o = board()
    source = with_ability("hero_only")
    zone(p, "hand", [source])
    old = p.active[0]
    reduce_evolve_pokemon_action(EvolvePokemonAction(p.id, source, old), s)
    assert p.active == [old] and source in p.hand


def test_borrowed_attack_cost_uses_attacker_tool_and_suppression():
    from packages.rules.borrowed_attacks import actions
    s, p, o = board()
    source = with_ability("borrow_evolutions")
    source.spec = {**source.spec, "abilities": [{"kind": "borrow_bench"}]}
    zone(p, "active", [source])
    source.energy = []
    assert not actions(p, s)
    source.energy = [CardType.ANY] * 6
    got = actions(p, s)
    assert got and all(a.source is source and a.effect_source is p.bench[0] for a in got)
    source.ability_blocked_turn = s.turn_number
    assert not actions(p, s)


def festival_board():
    s, p, o = board()
    source = with_ability("festival_lead")
    zone(p, "active", [source])
    source.attachment = [registry.get("P4E-001")() for _ in range(5)]
    source.energy = [CardType.ANY] * 5
    source.dynamic_energy = False
    # Use real energy matching this printed attack, preserving availability
    # after the first attack refreshes energy.
    energies = {"GRASS": "P4E-001", "WATER": "P4E-003", "PSYCHIC": "SVE-005"}
    source.attachment = [registry.get(energies.get(t.name, "SVE-008"))() for t in source.attacks[0].cost]
    spec = next(t for t in TRAINER_SPECS if t["name"] == "Festival Grounds")
    stadium = registry.get(spec["effectKey"])()
    stadium.playedFrom = p.id
    s.stadium = [stadium]
    target = o.active[0]
    target.hp, target.weakness, target.resistance = 1000, [], []
    env = object.__new__(RulesEngine)
    env.gamestate = s
    return env, s, p, o, source


def test_festival_runs_two_attacks_but_only_one_turn_transition():
    env, s, p, o, source = festival_board()
    turn = s.turn_number
    target = o.active[0]
    drive(env._apply_action(AttackAction(p.id, source, source.attacks[0], target)))
    assert s.turn_number == turn + 1
    assert len([e for e in p.attack_history if e["turn"] == turn]) == 2


def test_festival_can_decline_second_attack():
    env, s, p, o, source = festival_board()
    turn = s.turn_number
    drive(env._apply_action(AttackAction(p.id, source, source.attacks[0], o.active[0])), lambda acts, info, n: list(acts)[0])
    assert s.turn_number == turn + 1
    assert len([e for e in p.attack_history if e["turn"] == turn]) == 1
