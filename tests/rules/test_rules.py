import json
import random
import pytest
from loguru import logger
from ptcg.core.action import AttackAction, EvolvePokemonAction, PassTurn, UseAbilityAction, UseSupporterAction
from ptcg.core.card_registry import registry
from ptcg.core.enums import PlayerId, CardPosition, PokemonPosition
from ptcg.core.exceptions import InvalidDeckError
from packages.rules.adapter import Adapter
from packages.rules.engine import Gholdengo
from packages.rules.invariants import check_conservation
from packages.engine_adapter.base import policy

logger.remove()


def submit_option(game, value):
    view = game.view(game.actor)
    option = next(o for o in view['decision']['options'] if o['value'] == value)
    game.submit(game.actor, {'commandId': f't{game.version}', 'expectedStateVersion': game.version,
                            'decisionId': view['decision']['id'], 'choice': {'optionId': option['id']}})


def finish_setup(game, bonus=0, bench=False):
    for _ in range(2000):
        if not game.env.start_stage:
            return
        view = game.view(game.actor)
        d = view['decision']
        if d['kind'] == 'options':
            kind = d['options'][0]['kind']
            value = 'first' if kind == 'choose_order' else True if kind == 'ack_mulligan' else min(bonus, max(o['value'] for o in d['options']))
            submit_option(game, value)
        else:
            n = d['max'] if bench else d['min']
            game.submit(game.actor, {'commandId': f't{game.version}', 'expectedStateVersion': game.version,
                'decisionId': d['id'], 'choice': {'selectedRefs': [c['ref'] for c in d['candidates'][:n]]}})
    raise AssertionError('Setup did not finish')


def pass_turn(game):
    action = next(a for a in game.actions if isinstance(a, PassTurn))
    i = list(game.actions).index(action)
    game.submit(game.actor, {'commandId': f'p{game.version}', 'expectedStateVersion': game.version,
        'decisionId': f'd{game.version}', 'choice': {'optionId': f'd{game.version}:o{i}'}})


@pytest.mark.parametrize('choice', ['first', 'second'])
def test_order_before_hands(choice):
    game = Adapter(7)
    winner = game.actor
    for viewer in PlayerId:
        assert game.view(viewer)['observation']['self']['hand'] == []
    submit_option(game, choice)
    expected = winner if choice == 'first' else next(p for p in PlayerId if p != winner)
    assert game.env.gamestate.starting_player == expected


@pytest.mark.parametrize('seed', range(5))
def test_setup_secrecy_conservation_and_first_draw(seed):
    game = Adapter(seed)
    rng = random.Random(5)
    while game.env.start_stage:
        check_conservation(game.env.gamestate)
        for viewer in PlayerId:
            obs = game.view(viewer)['observation']
            assert obs['opponent']['active'] == [] and obs['opponent']['bench'] == []
        view = game.view(game.actor)
        command = policy(view, rng)
        if game.env.phase == 'mulligan_bonus':
            submit_option(game, 0)
        elif view['decision']['kind'] == 'selection' and view['decision']['min'] == 0:
            command['choice']['selectedRefs'] = []
            game.submit(game.actor, command)
        else:
            game.submit(game.actor, command)
    state = game.env.gamestate
    current = next(p for p in game.env.get_players() if p.id == state.turn)
    assert len(current.hand) == 7
    for p in game.env.get_players():
        assert len(p.prize) == 6
        assert len(p.active) == 1
    assert game.view(current.id)['observation']['opponent']['active']
    check_conservation(state)


def test_optional_bench_and_bonus():
    game = Adapter(7)
    finish_setup(game, bonus=999, bench=True)
    assert any(p.bench for p in game.env.get_players())
    assert any(game.env.gamestate.mulligans.values())
    assert any(e['kind'] == 'mulligan' for e in game.env.public_setup_events)
    for event in game.env.public_setup_events:
        if event['kind'] == 'mulligan':
            assert all(len(hand) == 7 for hand in event['hands'].values())
    check_conservation(game.env.gamestate)


def test_first_second_turn_attack_and_evolution():
    game = Adapter(1)
    finish_setup(game)
    state = game.env.gamestate
    for p in game.env.get_players():
        card = registry.get('PAF-007')()
        card.position, card.cardPosition = PokemonPosition.ACTIVE, CardPosition.ACTIVE
        card.energy = list(card.attacks[0].cost)
        card.firstTurnPlayed = False
        p.active = [card]
        p.hand = [registry.get('PAF-008')()]
        state.turn = p.id
        actions = p.get_actions(state)
        assert bool([a for a in actions if isinstance(a, AttackAction)]) == (p.id != state.starting_player)
        assert not any(isinstance(a, EvolvePokemonAction) for a in actions)


def test_first_player_supporter_prohibited_second_allowed():
    game = Adapter(0)
    finish_setup(game)
    for p in game.env.get_players():
        p.hand = [registry.get('PAF-087')()]
        game.env.gamestate.turn = p.id
        assert bool([a for a in p.get_actions(game.env.gamestate) if isinstance(a, UseSupporterAction)]) == (p.id != game.env.gamestate.starting_player)


def test_two_ace_specs_rejected_at_engine_entry():
    deck = ['1 Charmander PAF 7', '1 Prime Catcher TEF 157', '1 Master Ball TEF 153', '57 Fire Energy SVE 2']
    with pytest.raises(InvalidDeckError, match='ACE_SPEC_LIMIT'):
        Adapter(deck1=deck)


def test_gholdengo_independent_once_per_instance():
    game = Adapter(0)
    finish_setup(game)
    state = game.env.gamestate
    p = next(p for p in game.env.get_players() if p.id == state.turn)
    a, b = Gholdengo(), Gholdengo()
    a.position, b.position = PokemonPosition.ACTIVE, PokemonPosition.BENCH
    p.active, p.bench = [a], [b]
    before = len(p.hand)
    action = next(a1 for a1 in a.get_actions(state) if isinstance(a1, UseAbilityAction))
    list(a.reduce_action(action, state))
    assert len(p.hand) == before + 2
    assert not any(isinstance(x, UseAbilityAction) for x in a.get_actions(state))
    action = next(x for x in b.get_actions(state) if isinstance(x, UseAbilityAction))
    list(b.reduce_action(action, state))
    assert len(p.hand) == before + 3
    assert not p.onceUsedTurn.get('Coin Bonus', False)


@pytest.mark.parametrize('seed', [0, 7, 42])
def test_replay_every_setup_prompt(seed):
    game = Adapter(seed)
    rng = random.Random(seed)
    while game.env.start_stage:
        restored = Adapter.replay(game.export_private_replay())
        actor = game.actor
        assert game.view(actor) == restored.view(actor)
        command = policy(game.view(actor), rng)
        game.submit(actor, command)
        restored.submit(actor, command)
        assert game.private_digest() == restored.private_digest()


def test_turn_transition_replay():
    game = Adapter(0)
    finish_setup(game)
    pass_turn(game)
    restored = Adapter.replay(game.export_private_replay())
    assert restored.private_digest() == game.private_digest()
