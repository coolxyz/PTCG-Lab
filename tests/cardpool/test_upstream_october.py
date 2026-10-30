"""Directed settlement of October printings, independent of admission flags."""
import pytest
import json
from pathlib import Path
from ptcg.core.card_registry import registry
from ptcg.core.enums import CardType, PokemonType, SpecialCondition
from ptcg.core.action import AttackAction, UseItemAction
from packages.rules import field_effects, transfer_attacks
from packages.rules.core_fixes import refresh_energy, shield_damage
from packages.rules.modifiers import attack_damage
from test_effects import context, zone, drive, energy

IMPLEMENTATIONS = json.loads(Path('data/sync/implementations.json').read_text())['cards']


def card(cid):
    return registry.get(IMPLEMENTATIONS[str(cid)]['effectKey'])()


def board(cid, index=0):
    s, p, o = context()
    source = card(cid)
    zone(p, 'active', [source])
    zone(p, 'hand', [])
    zone(o, 'bench', [])
    target = o.active[0]
    target.hp = 1000
    target.weakness = target.resistance = []
    source.energy = list(source.attacks[index].cost)
    return s, p, o, source, target, AttackAction(p.id, source, source.attacks[index], target)


@pytest.mark.parametrize('count,damage', [(0, 0), (1, 0), (2, 130), (3, 130)])
def test_lurantis_requires_two_physical_basic_grass_from_hand(count, damage):
    s, p, o, source, target, action = board(22176, 1)
    grass = [registry.get('P4E-001')() for _ in range(count)]
    wrong = energy()
    zone(p, 'hand', grass + [wrong])
    drive(source.reduce_action(action, s))
    assert target.hp == 1000 - damage
    assert sum(c in p.discard for c in grass) == (2 if count >= 2 else 0)
    assert wrong not in p.discard


def test_jynx_transfer_only_to_newly_benched_basic():
    s, p, o, source, target, action = board(22183)
    old = card(22198)
    new = card(22194)
    evolved = card(22176)
    zone(p, 'bench', [old])
    zone(p, 'left', [evolved, new])
    attached = energy()
    source.attachment = [attached]
    refresh_energy(source)
    drive(transfer_attacks.resolve({'operation': 'search_bench_transfer'}, action, s))
    assert p.bench == [old, new]
    assert attached in new.attachment and not source.attachment and not old.attachment
    assert evolved in p.left and new.firstTurnPlayed


@pytest.mark.parametrize('full,decline', [(True, False), (False, True)])
def test_jynx_cannot_transfer_without_new_bench_target(full, decline):
    s, p, o, source, target, action = board(22183)
    zone(p, 'bench', [card(22198) for _ in range(p.benchSize if full else 1)])
    zone(p, 'left', [card(22194)])
    attached = energy()
    source.attachment = [attached]
    def picker(actions, info, n):
        return actions.action_for_candidate_indices([])
    drive(transfer_attacks.resolve({'operation': 'search_bench_transfer'}, action, s), picker if decline else None)
    assert source.attachment == [attached]
    assert all(not c.attachment for c in p.bench)


@pytest.mark.parametrize('cid,both', [(22188, True), (22199, False)])
def test_bench_splash_hits_only_printed_sides_without_weakness(cid, both):
    s, p, o, source, target, action = board(cid)
    own, enemy = card(22198), card(22198)
    zone(p, 'bench', [own])
    zone(o, 'bench', [enemy])
    own.weakness = enemy.weakness = [source.cardType]
    before = (own.hp, enemy.hp, source.hp, target.hp)
    drive(field_effects.resolve({'kind': 'own_bench_damage', 'amount': 10, 'both': both}, action, s))
    assert (own.hp, enemy.hp) == (before[0] - 10, before[1] - (10 if both else 0))
    assert (source.hp, target.hp) == before[2:]


@pytest.mark.parametrize('attached,expected', [(False, 60), (True, 30)])
def test_regirock_armor_requires_attached_energy(attached, expected):
    s, p, o, source, target, action = board(22203)
    source.attachment = [energy()] if attached else []
    refresh_energy(source)
    assert shield_damage(source, 60, s, target) == expected


def test_sylveon_ex_shield_blocks_damage_but_not_effects():
    from packages.rules.protection import blocked
    s, p, o, source, target, action = board(22196)
    target.pokemonType = PokemonType.EX
    assert blocked(source, s, 'damage', target)
    assert not blocked(source, s, 'effects', target)
    target.pokemonType = PokemonType.NORMAL
    assert not blocked(source, s, 'damage', target)


def test_beheeyem_counts_own_active_and_bench_only():
    from packages.rules.attack_math import damage
    s, p, o, source, target, action = board(22195)
    zone(p, 'bench', [card(22198), card(22194)])
    zone(o, 'bench', [card(22198) for _ in range(4)])
    assert damage(source.spec['attacks'][0]['mechanic'], action, s) == 60


@pytest.mark.parametrize('same,expected', [(True, 170), (False, 50)])
def test_medicham_bonus_counts_energy_units(same, expected):
    from packages.rules.attack_math import damage
    s, p, o, source, target, action = board(22202)
    source.energy = [CardType.FIGHTING, CardType.COLORLESS]
    target.energy = [CardType.METAL] * (2 if same else 1)
    assert damage(source.spec['attacks'][0]['mechanic'], action, s) == expected


def test_skarmory_heals_and_locks_retreat_only_next_own_turn():
    s, p, o, source, target, action = board(22207)
    source.hp -= 70
    before, turn = source.hp, s.turn_number
    drive(source.after_damage(action, s))
    assert source.hp == before + 50
    assert source.retreat_blocked_turn == turn + 2


def test_komala_next_turn_bonus_targets_opposing_active_only():
    s, p, o, source, target, action = board(22212)
    turn = s.turn_number
    drive(source.after_damage(action, s))
    assert source.special_condition == target.special_condition == SpecialCondition.ASLEEP
    assert attack_damage(source, target, 30, s) == 30
    s.turn_number = turn + 2
    assert attack_damage(source, target, 30, s) == 130
    bench = card(22198)
    zone(o, 'bench', [bench])
    assert attack_damage(source, bench, 30, s) == 30
    s.turn_number += 2
    assert attack_damage(source, target, 30, s) == 30


def test_squawkabilly_opponent_selects_replacement_and_can_decline():
    s, p, o, source, target, action = board(22213)
    bench = card(22198)
    zone(o, 'bench', [bench])
    infos = drive(source.after_damage(action, s))
    assert o.active == [bench]
    assert list(infos[-1]['raw_available_actions'])[0].playerId == o.id
    s, p, o, source, target, action = board(22213)
    zone(o, 'bench', [card(22198)])
    drive(source.after_damage(action, s), lambda actions, info, n: actions.action_for_candidate_indices([]))
    assert o.active == [target]


def test_forretress_moves_only_original_metal_energy_once():
    s, p, o, source, target, action = board(22206)
    recipient = card(22198)
    zone(p, 'bench', [recipient])
    metal, grass = energy(), registry.get('P4E-001')()
    source.attachment = [metal, grass]
    refresh_energy(source)
    drive(source.after_damage(action, s))
    assert source.attachment == [grass] and recipient.attachment == [metal]
    assert source.energy == [CardType.GRASS] and recipient.energy == [CardType.METAL]


def test_darmanitan_recoil_is_damage_after_the_attack():
    s, p, o, source, target, action = board(22181, 1)
    hp = source.hp
    drive(source.reduce_action(action, s))
    assert source.hp == hp - 70 and target.hp == 790


def test_energy_switch_new_wording_moves_one_basic_card():
    s, p, o = context()
    tool = card(22014)
    zone(p, 'hand', [tool])
    donor, recipient = p.active[0], card(22198)
    zone(p, 'bench', [recipient])
    attached = energy()
    donor.attachment = [attached]
    refresh_energy(donor)
    drive(tool.reduce_action(UseItemAction(p.id, tool), s))
    assert attached in recipient.attachment and attached not in donor.attachment
    assert tool in p.discard


@pytest.mark.parametrize('cid', [22336, 22337, 22341])
def test_reward_pack_tera_ex_has_two_prizes_and_bench_protection(cid):
    s, p, o, source, target, action = board(cid)
    assert source.pokemonType == PokemonType.EX and source.prize == 2
    assert source.pokemonRule.name == 'TERA'


def test_evolution_tool_printed_attack_keeps_one_colorless_cost():
    from packages.rules.adapter import Adapter
    lines = ['1 Technical Machine: Evolution PAR 178', '1 Mew ex MEW 151', '58 Metal Energy SVE 008']
    game = Adapter(0, lines, lines)
    tool = next(c for c in game.env.gamestate.player1.deck if c.id == 'PAR-178')
    assert tool.id == 'PAR-178'
    # The actual native class provides this attack to its physical holder.
    assert tool.attacks[0].name == 'Evolution'
    assert tool.attacks[0].cost == [CardType.COLORLESS]
