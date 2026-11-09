"""Directed boundary tests for the November card rule primitives."""
import pytest
from ptcg.core.action import AttackAction, EvolvePokemonAction, UseItemAction, UseToolAction
from ptcg.core.enums import CardType, Stage

from packages.rules.core_fixes import check_energy, refresh_energy
from packages.rules.damage_events import deal, finish
from packages.rules.end_phase import finish as finish_end, needed
from packages.rules.hand_locks import allowed
from packages.rules.maximum_hp import maximum, reconcile
from packages.rules.plain import PlainPokemon
from packages.rules.protection import blocked
from packages.rules.special_energy import CompiledEnergy
from scripts.sync.prepare_november_2026 import ABILITIES, ATTACKS, ENERGIES
from test_effects import context, drive, zone


def pokemon(name, ability=None, *, stage=Stage.BASIC, predecessor=None):
    spec = {
        'effectKey': 'P4P-NOVTEST', 'name': name, 'hp': 160, 'type': 'PSYCHIC',
        'stage': stage.name, 'evolvesFrom': [predecessor] if predecessor else [],
        'retreat': 1, 'weakness': [], 'resistance': [],
        'attacks': [{'name': '回声', 'cost': ['PSYCHIC'], 'damage': 30}],
        'abilities': [{'name': '测试', 'text': '', **ability}] if ability else [],
    }
    return type('FixturePokemon', (PlainPokemon,), {'spec': spec})()


def special(cid):
    name, mechanic = ENERGIES[cid]
    spec = {'effectKey': 'P4S-NOVTEST' + cid, 'name': name, 'text': '', 'energyType': 'special', 'mechanic': mechanic}
    return type('FixtureEnergy', (CompiledEnergy,), {'spec': spec})()


def test_first_turn_attack_uses_ability_without_waiving_energy(monkeypatch):
    state, player, _ = context()
    player.firstTurn = True
    state.starting_player = player.id
    source = pokemon('Meloetta ex', ABILITIES['22502'])
    zone(player, 'active', [source])

    def attacks():
        return [a for a in player.get_actions(state) if isinstance(a, AttackAction)]

    assert not attacks()
    source.energy = [CardType.PSYCHIC]
    assert [a.source for a in attacks()] == [source]
    monkeypatch.setattr('packages.rules.abilities.enabled', lambda card, state: False)
    assert not attacks()


@pytest.mark.parametrize('first_turn', [False, True])
def test_accelerated_evolution_requires_partner_and_respects_locks(first_turn):
    state, player, _ = context()
    state.starting_player = player.id
    player.firstTurn = first_turn
    base = pokemon('Karrablast', ABILITIES['22464'])
    zone(player, 'active', [base])
    base.firstTurnPlayed = True
    evolution = pokemon('Escavalier', stage=Stage.STAGE_1, predecessor='Karrablast')
    zone(player, 'hand', [evolution])

    def can_evolve():
        return any(isinstance(a, EvolvePokemonAction) and a.target is base and a.source is evolution
                   for a in player.get_actions(state))

    assert not can_evolve()
    zone(player, 'bench', [pokemon('Shelmet')])
    assert can_evolve()
    base.evolution_blocked_turn = state.turn_number
    assert not can_evolve()


def test_item_and_tool_lock_is_active_only_and_hand_tool_only():
    state, player, opponent = context()
    holder = pokemon('Jellicent ex', ABILITIES['22831'])
    zone(opponent, 'active', [holder])
    item, tool = object(), object()
    player.hand = [item, tool]
    item_action = UseItemAction(player.id, item)
    tool_action = UseToolAction(player.id, tool, player.active[0])
    assert not allowed(item_action, player, state)
    assert not allowed(tool_action, player, state)
    player.hand.remove(tool)
    assert allowed(tool_action, player, state)
    zone(opponent, 'active', [pokemon('Unrelated')])
    zone(opponent, 'bench', [holder])
    assert allowed(item_action, player, state)


def test_prism_is_one_unit_and_ignition_expires_on_owners_turn():
    state, player, opponent = context()
    basic, evolved = pokemon('Basic'), pokemon('Evolution', stage=Stage.STAGE_1)
    zone(player, 'active', [basic])
    zone(player, 'bench', [evolved])
    prism, ignition, other = special('22455'), special('22875'), special('22875')
    basic.attachment, evolved.attachment, opponent.active[0].attachment = [prism], [ignition], [other]
    refresh_energy(basic, state)
    refresh_energy(evolved, state)
    assert basic.energy == [CardType.ANY]
    assert not check_energy([CardType.FIRE, CardType.WATER], basic.energy)
    assert evolved.energy == [CardType.COLORLESS] * 3
    assert prism.effective_provides(evolved) == [CardType.COLORLESS]
    assert ignition.effective_provides(basic) == [CardType.COLORLESS]
    assert needed(state)
    state.pending_end_phase = True
    drive(finish_end(state))
    assert ignition in player.discard and not evolved.attachment and not evolved.energy
    assert other in opponent.active[0].attachment and prism in basic.attachment


def test_typed_energy_hp_recalculates_without_removing_damage_counters():
    state, player, _ = context()
    source = pokemon('Conkeldurr', ABILITIES['22507'])
    zone(player, 'active', [source])
    source.hp -= 40
    source.energy = [CardType.FIGHTING, CardType.FIGHTING, CardType.METAL]
    reconcile(state)
    assert (maximum(source), source.hp) == (240, 200)
    source.energy.pop(0)
    reconcile(state)
    assert (maximum(source), source.hp) == (200, 160)


def test_special_energy_shield_blocks_damage_and_effects_only_from_its_holder():
    state, player, opponent = context()
    target = pokemon('Carracosta', ABILITIES['22481'])
    zone(opponent, 'active', [target])
    attacker = player.active[0]
    assert not blocked(target, state, 'damage', attacker)
    attacker.attachment = [special('22455')]
    assert blocked(target, state, 'damage', attacker)
    assert blocked(target, state, 'effects', attacker)


@pytest.mark.parametrize('stage,damage,prizes', [(Stage.BASIC, 170, 1), (Stage.STAGE_1, 170, 0), (Stage.BASIC, 100, 0)])
def test_bonus_prize_requires_basic_knocked_out_by_attack_damage(stage, damage, prizes):
    state, player, opponent = context()
    source = pokemon('Hydreigon ex', ABILITIES['22851'])
    target = pokemon('Target', stage=stage)
    zone(player, 'active', [source])
    zone(opponent, 'active', [target])
    deal(source, target, damage, state)
    finish(state)
    assert getattr(target, 'knockout_bonus_prizes', 0) == prizes


def test_victini_does_zero_damage_with_four_bench_and_printed_damage_with_five():
    for bench_count in (4, 5):
        state, player, opponent = context()
        source, target = pokemon('Victini'), pokemon('Target')
        zone(player, 'active', [source])
        zone(opponent, 'active', [target])
        zone(player, 'bench', [pokemon(f'Bench {i}') for i in range(bench_count)])
        source.spec['attacks'][0]['mechanic'] = ATTACKS['22467'][0]
        source.attacks[0].damage = 120
        drive(source.reduce_action(AttackAction(player.id, source, source.attacks[0], target), state))
        assert target.hp == (160 if bench_count == 4 else 40)


def test_accelgor_inflicts_both_conditions_and_switches_to_bench():
    from ptcg.core.enums import SpecialCondition

    state, player, opponent = context()
    source, replacement, target = pokemon('Accelgor'), pokemon('Replacement'), pokemon('Target')
    zone(player, 'active', [source])
    zone(player, 'bench', [replacement])
    zone(opponent, 'active', [target])
    source.spec['attacks'][0]['mechanic'] = ATTACKS['22368'][0]
    source.attacks[0].damage = 70
    drive(source.reduce_action(AttackAction(player.id, source, source.attacks[0], target), state))
    assert player.active == [replacement] and player.bench == [source]
    assert target.hp == 90 and target.poisoned and target.special_condition == SpecialCondition.CONFUSED
